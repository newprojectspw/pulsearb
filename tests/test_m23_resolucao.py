"""M2.3 — o backtest não lia as resoluções que o recorder grava.

Gravação de produção de 2026-08-19 19h UTC: 73 eventos `market_resolved`
gravados, `janelas_com_resolucao: 0` no relatório. O evento chegava e era
gravado; o leitor não o reconhecia.

Causa: o leitor procurava `asset_id` (singular). O evento real traz
`assets_ids` (lista) e `winning_asset_id`, e nenhum `asset_id`. A linha
`if not isinstance(asset_id, str): continue` descartava todos os 73, em
silêncio.

Toda fixture de forma de evento aqui é **captura real** de produção
(`tests/fixtures/clob_ws_market_resolved.json`). Uma fixture sintética teria a
forma que nós imaginamos — que é justamente a errada, e foi ela que produziu o
defeito.
"""

from __future__ import annotations

import gzip
import json
import time
from pathlib import Path

import orjson

from pulsearb.backtest.__main__ import RecordingIndex
from pulsearb.feeds.poly_ws import (
    normalizar_condition_id,
    resolucao_do_evento,
)
from pulsearb.replay.reader import RecordingReader

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "clob_ws_market_resolved.json").read_text(
        encoding="utf-8"
    )
)
LINHA_REAL = FIXTURE["linha_jsonl"]
EVENTO_REAL = LINHA_REAL["payload"]
CONDITION_ID = EVENTO_REAL["market"]
TOKEN_VENCEDOR, TOKEN_PERDEDOR = EVENTO_REAL["assets_ids"]
# O evento diz "Up" e nomeia o primeiro token como vencedor.
assert EVENTO_REAL["winning_asset_id"] == TOKEN_VENCEDOR


# ═══════════════════════════════════════════════ o parser do evento


def test_evento_real_nao_tem_asset_id_singular():
    """O campo que o leitor antigo procurava simplesmente não existe.

    Este teste existe para que a causa fique registrada no código, e não só
    numa mensagem de commit: se um dia o servidor passar a mandar `asset_id`,
    é aqui que se descobre.
    """
    assert "asset_id" not in EVENTO_REAL
    assert isinstance(EVENTO_REAL["assets_ids"], list)
    assert len(EVENTO_REAL["assets_ids"]) == 2


def test_parser_le_a_forma_real():
    resolucao = resolucao_do_evento(EVENTO_REAL)

    assert resolucao is not None
    assert resolucao.condition_id == normalizar_condition_id(CONDITION_ID)
    assert set(resolucao.tokens) == {TOKEN_VENCEDOR, TOKEN_PERDEDOR}
    assert resolucao.winning_token_id == TOKEN_VENCEDOR
    assert resolucao.winning_outcome == "Up"
    # `timestamp` vem em MILISSEGUNDOS, como string.
    assert resolucao.ts_servidor_ms == 1787166722776.0
    assert resolucao.sintetico is False


def test_identidade_do_token_decide_o_lado():
    """`winning_asset_id` decide sem depender do rótulo do outcome."""
    resolucao = resolucao_do_evento(EVENTO_REAL)
    assert resolucao is not None
    assert resolucao.venceu_up(TOKEN_VENCEDOR, TOKEN_PERDEDOR) is True
    # Mesmo evento, mercado montado ao contrário: o vencedor é o Down.
    assert resolucao.venceu_up(TOKEN_PERDEDOR, TOKEN_VENCEDOR) is False


def test_condition_id_normaliza_grafia():
    """Comparar `0xABE6…` com `abe6…` falharia em silêncio."""
    assert normalizar_condition_id("0xABE6E9") == "abe6e9"
    assert normalizar_condition_id("abe6e9") == "abe6e9"
    assert normalizar_condition_id("  0xAbE6E9  ") == "abe6e9"
    assert normalizar_condition_id(None) is None
    assert normalizar_condition_id("0x") is None


def test_fallback_sintetico_da_gamma_continua_aceito():
    """O recorder grava a resolução vinda da Gamma com outra forma.

    Ela tem `asset_id` e não tem `assets_ids`. Aceitar as duas é o mesmo
    princípio do `price_change` (API_NOTES 6.1b): o caminho independente só
    serve se o leitor souber lê-lo.
    """
    resolucao = resolucao_do_evento(
        {
            "_sintetico": True,
            "event_type": "market_resolved",
            "asset_id": "up1",
            "market": "0xdead",
            "winning_outcome": "Down",
        }
    )
    assert resolucao is not None
    assert resolucao.sintetico is True
    assert resolucao.tokens == ("up1",)
    assert resolucao.venceu_up("up1", "dn1") is False


def test_evento_de_outro_tipo_nao_vira_resolucao():
    assert resolucao_do_evento({"event_type": "book", "asset_id": "x"}) is None


# ══════════════════════════════════ o caminho completo, sobre gravação


def _gravacao(
    tmp_path: Path,
    *,
    condition_id: str = CONDITION_ID,
    token_up: str = TOKEN_VENCEDOR,
    token_down: str = TOKEN_PERDEDOR,
    com_resolucao: bool = True,
) -> Path:
    """Descoberta + a LINHA REAL de resolução, copiada da produção."""
    fim = int(LINHA_REAL["ts_wall_ns"] / 1e9) - 120
    linhas = [
        {
            "ts_mono_ns": 1,
            "ts_wall_ns": int((fim - 300) * 1e9),
            "fonte": "discovery_snapshot",
            "payload": {
                "janelas": [
                    {
                        "slug": "bitcoin-up-or-down-5m-teste",
                        "condition_id": condition_id,
                        "asset": "btc",
                        "resolution": "twap_sixty",
                        "end_date_iso": time.strftime(
                            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(fim)
                        ),
                        "tick_size": 0.01,
                        "token_id_by_outcome": {"Up": token_up, "Down": token_down},
                    }
                ]
            },
        }
    ]
    if com_resolucao:
        linhas.append(LINHA_REAL)

    caminho = tmp_path / "rec.jsonl.gz"
    with gzip.open(caminho, "wb") as handle:
        for linha in linhas:
            handle.write(orjson.dumps(linha) + b"\n")
    return caminho


def _indexar(caminho: Path) -> RecordingIndex:
    index = RecordingIndex(RecordingReader(caminho))
    index.build()
    return index


def test_janela_ganha_resolucao_a_partir_do_evento_real(tmp_path):
    """O teste que teria pego o defeito: 1 evento gravado, 1 janela resolvida."""
    index = _indexar(_gravacao(tmp_path))
    janelas = index.janelas()

    assert len(janelas) == 1
    assert janelas[0].resolveu_up is True
    assert len([j for j in janelas if j.resolveu_up is not None]) == 1


def test_casamento_sobrevive_a_grafia_do_condition_id(tmp_path):
    """A descoberta pode gravar o condition id em outra caixa que o WS."""
    index = _indexar(_gravacao(tmp_path, condition_id=CONDITION_ID.upper()))
    assert index.janelas()[0].resolveu_up is True

    sem_prefixo = CONDITION_ID.removeprefix("0x")
    index = _indexar(_gravacao(tmp_path, condition_id=sem_prefixo))
    assert index.janelas()[0].resolveu_up is True


def test_lado_perdedor_resolve_para_false(tmp_path):
    """Mesmo evento real, mercado com Up/Down trocados."""
    index = _indexar(
        _gravacao(tmp_path, token_up=TOKEN_PERDEDOR, token_down=TOKEN_VENCEDOR)
    )
    assert index.janelas()[0].resolveu_up is False


def test_atraso_de_liquidacao_usa_o_carimbo_do_servidor(tmp_path):
    """A pergunta é quanto a plataforma demorou, não quanto a nossa rede."""
    index = _indexar(_gravacao(tmp_path))
    esperado = int(1787166722776 * 1e6)
    assert index.resolucoes[TOKEN_VENCEDOR] == esperado
    # a chegada local foi ~18ms depois; usar ela mediria a nossa latência junto
    assert LINHA_REAL["ts_wall_ns"] != esperado


def test_resumo_conta_evento_e_janela_separadamente(tmp_path):
    index = _indexar(_gravacao(tmp_path))
    resumo = index.resolucoes_resumo(index.janelas())

    assert resumo["eventos_lidos"] == 1
    assert resumo["mercados_distintos"] == 1
    assert resumo["janelas_casadas"] == 1
    assert resumo["sinteticas_via_gamma"] == 0
    assert resumo["resolucoes_sem_janela_correspondente"] == 0


def test_token_casa_mesmo_com_condition_id_divergente(tmp_path):
    """Os dois caminhos são independentes de propósito.

    Se a descoberta gravou um condition id que não bate com o do WS, o
    casamento por token ainda salva a janela. Perder uma resolução custa a
    janela inteira no backtest; ter duas chaves é barato.
    """
    index = _indexar(_gravacao(tmp_path, condition_id="0xoutromercado"))
    assert index.janelas()[0].resolveu_up is True


def test_resolucao_orfa_aparece_no_relatorio(tmp_path):
    """Resolução de mercado que a descoberta nunca viu não some em silêncio.

    Acontece de verdade: janela que nasceu antes do recorder subir, ou de
    ativo fora dos configurados. O evento chega, não casa com nada, e tem de
    aparecer contado em vez de sumir.
    """
    index = _indexar(
        _gravacao(
            tmp_path,
            condition_id="0xoutromercado",
            token_up="outro-up",
            token_down="outro-down",
        )
    )
    resumo = index.resolucoes_resumo(index.janelas())

    assert resumo["eventos_lidos"] == 1
    assert resumo["janelas_casadas"] == 0
    assert resumo["resolucoes_sem_janela_correspondente"] == 1
    assert normalizar_condition_id(CONDITION_ID) in resumo["condicoes_orfas"]


def test_sem_evento_a_janela_fica_sem_resolucao(tmp_path):
    """Controle: o casamento não pode inventar resultado onde não houve."""
    index = _indexar(_gravacao(tmp_path, com_resolucao=False))
    janelas = index.janelas()

    assert janelas[0].resolveu_up is None
    assert index.resolucoes_resumo(janelas)["eventos_lidos"] == 0


# ═══════════════ M2.3-b — contadores em contradição (invariantes)
# Rodada real de 2026-08-19: `janelas_casadas: 26` e, no MESMO relatório,
# `janelas_com_resolucao: 0` com a âncora sem amostra. Causa: o filtro de
# integridade ficava ENTRE o casamento e o contador, e a âncora consumia o
# conjunto já filtrado — sendo que a âncora usa stream RTDS + resolução e
# nunca lê o livro. Estes testes fixam a ordem correta dos filtros.


def _rodar_backtest(caminho: Path) -> dict:
    import contextlib
    import io

    from pulsearb.backtest.__main__ import main

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert main([str(caminho.parent)]) == 0
    return json.loads(buf.getvalue())


def _linha_poly(ts_ns: int, payload: dict) -> dict:
    return {
        "ts_mono_ns": ts_ns,
        "ts_wall_ns": ts_ns,
        "fonte": "poly_ws",
        "payload": payload,
    }


def _gravacao_com_livro_corrompido(tmp_path: Path) -> Path:
    """Descoberta + resolução REAL + livro que diverge do topo afirmado.

    O delta afirma um topo (0.90) incompatível com o livro reconstruído
    (0.49) — magnitude 0.41, muito acima do limiar de invalidação (0.01).
    A janela fica com resultado E com livro condenado, que é exatamente o
    estado das 26 janelas da rodada real.
    """
    caminho = _gravacao(tmp_path)
    base = int(LINHA_REAL["ts_wall_ns"]) - 240 * 10**9
    with gzip.open(caminho, "ab") as handle:
        for linha in (
            _linha_poly(
                base,
                {
                    "event_type": "book",
                    "asset_id": TOKEN_VENCEDOR,
                    "timestamp": str(base // 10**6),
                    "bids": [{"price": "0.49", "size": "100"}],
                    "asks": [{"price": "0.51", "size": "100"}],
                },
            ),
            _linha_poly(
                base + 10**9,
                {
                    "event_type": "price_change",
                    "market": CONDITION_ID,
                    "timestamp": str((base + 10**9) // 10**6),
                    "price_changes": [
                        {
                            "asset_id": TOKEN_VENCEDOR,
                            "price": "0.49",
                            "size": "50",
                            "side": "BUY",
                            "best_bid": "0.90",
                            "best_ask": "0.95",
                        }
                    ],
                },
            ),
        ):
            handle.write(orjson.dumps(linha) + b"\n")
    return caminho


def test_invariante_casada_implica_contada_e_ancora_alimentada(tmp_path):
    """janelas_casadas > 0 ⇒ janelas_com_resolucao > 0 e âncora com amostra."""
    rel = _rodar_backtest(_gravacao(tmp_path))

    casadas = rel["gravacao"]["resolucoes"]["janelas_casadas"]
    assert casadas == 1
    assert rel["gravacao"]["janelas_com_resolucao"] == casadas
    assert rel["ancora"]["janelas_alimentadas"] == casadas


def test_livro_corrompido_nao_tira_a_janela_da_ancora(tmp_path):
    """A invalidação por integridade vale para FILLS, não para a âncora.

    A âncora compara stream RTDS com o resultado; o livro não entra nessa
    conta. Condenar a janela inteira por livro furado foi o que deixou a
    âncora sem amostra numa gravação com 26 resoluções casadas.
    """
    rel = _rodar_backtest(_gravacao_com_livro_corrompido(tmp_path))

    # a janela foi invalidada para fills...
    assert rel["integridade"]["janelas_invalidadas"] == ["bitcoin-up-or-down-5m-teste"]
    assert rel["backtest"]["janelas_avaliaveis"] == 0
    assert rel["backtest"]["janelas_excluidas_por_integridade"] == 1
    # ...mas continua contada e continua alimentando a âncora
    assert rel["gravacao"]["janelas_com_resolucao"] == 1
    assert rel["ancora"]["janelas_alimentadas"] == 1


def test_zero_amostras_na_ancora_nao_vira_falsificacao(tmp_path):
    """SEM DADO ≠ 'nenhuma hipótese sobreviveu'.

    Com zero janelas avaliadas todas as hipóteses têm total_avaliado = 0 e
    `sobreviveu` False — o texto antigo declarava falsificação em cima do
    vazio. O veredito agora distingue os dois casos.
    """
    rel = _rodar_backtest(_gravacao(tmp_path, com_resolucao=False))

    assert rel["gravacao"]["janelas_com_resolucao"] == 0
    assert rel["ancora"]["janelas_alimentadas"] == 0
    assert rel["ancora"]["veredito"].startswith("SEM DADO")
    assert "sobreviveu" not in rel["ancora"]["veredito"]


def test_eventos_redundantes_sao_deduplicados_e_contados(tmp_path):
    """73 eventos → 26 mercados na rodada real: fio + 2 sintéticos por janela.

    O fallback da Gamma grava um evento por TOKEN, e o gate `resolvidos` do
    recorder não reconhece a forma do fio (procura `asset_id`, que o evento
    real não tem). O leitor deduplica por mercado e reporta a redundância.
    """
    caminho = _gravacao(tmp_path)
    ts = int(LINHA_REAL["ts_wall_ns"]) + 10**9
    with gzip.open(caminho, "ab") as handle:
        for indice, token in enumerate((TOKEN_VENCEDOR, TOKEN_PERDEDOR)):
            linha = {
                "ts_mono_ns": ts + indice,
                "ts_wall_ns": ts + indice,
                "fonte": "resolucao_via_gamma",
                "payload": {
                    "_sintetico": True,
                    "event_type": "market_resolved",
                    "asset_id": token,
                    "market": CONDITION_ID,
                    "winning_outcome": "Up",
                },
            }
            handle.write(orjson.dumps(linha) + b"\n")

    index = _indexar(caminho)
    resumo = index.resolucoes_resumo(index.janelas())

    assert resumo["eventos_lidos"] == 3
    assert resumo["eventos_do_fio"] == 1
    assert resumo["sinteticas_via_gamma"] == 2
    assert resumo["eventos_redundantes"] == 2
    assert resumo["mercados_distintos"] == 1
    assert resumo["conflitos_fio_vs_gamma"] == []
    # e a dedup preserva o evento do fio como fonte da verdade
    assert index.janelas()[0].resolveu_up is True


def test_sintetico_nao_sobrescreve_o_fio_e_conflito_e_contado(tmp_path):
    """Gamma discordando do fio é anomalia grave — contada, nunca engolida."""
    caminho = _gravacao(tmp_path)
    ts = int(LINHA_REAL["ts_wall_ns"]) + 10**9
    with gzip.open(caminho, "ab") as handle:
        linha = {
            "ts_mono_ns": ts,
            "ts_wall_ns": ts,
            "fonte": "resolucao_via_gamma",
            "payload": {
                "_sintetico": True,
                "event_type": "market_resolved",
                "asset_id": TOKEN_VENCEDOR,
                "market": CONDITION_ID,
                "winning_outcome": "Down",
            },
        }
        handle.write(orjson.dumps(linha) + b"\n")

    index = _indexar(caminho)
    resumo = index.resolucoes_resumo(index.janelas())

    assert resumo["conflitos_fio_vs_gamma"] == [normalizar_condition_id(CONDITION_ID)]
    # o fio ("Up", com winning_asset_id) continua valendo
    assert index.janelas()[0].resolveu_up is True
