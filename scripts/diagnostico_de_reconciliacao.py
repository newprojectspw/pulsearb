"""Diagnóstico offline da reconciliação do livro CLOB.

    python scripts/diagnostico_de_reconciliacao.py data/recordings/pulsearb-20260924-1900.jsonl.gz

Existe porque a causa raiz das divergências PERSISTENTES e dos tokens
COMPROMETIDOS não se lê do relatório agregado — ela é por token, e depende de
distinguir quatro coisas que o número total confunde:

1. **corrida de um tick** — o `best_bid_ask` e o `price_change` que a corrige
   ainda não se cruzaram. NORMAL (M2.5), some com o alinhamento por carimbo.
2. **snapshot com profundidade truncada** — o servidor afirma um topo num
   nível que nunca nos foi enviado (estava abaixo da profundidade do snapshot).
   A reconstrução fica errada e NÃO se conserta com delta nenhum.
3. **delta perdido** — a fila transbordou (incidente) e um delta sumiu. Aqui
   NÃO aconteceu (`incidentes_de_fila = 0`), mas o diagnóstico o distingue.
4. **resync não reproduzido no backtest** — o recorder, ao vivo, marcou perda
   e reassinou (o `book` de recuperação está gravado como `resync_book` +
   `book`). O backtest, porém, NÃO chama `marcar_perda`, então a sua
   reconstrução nunca é re-ancorada e a divergência que o recorder já tinha
   consertado ao vivo PERSISTE no backtest — um descompasso de *mesmo caminho*.

A ferramenta roda o MESMO `MonitorDeIntegridade` do backtest sobre a gravação,
em dois modos — sem e com a reprodução dos resyncs (`--replay-resync`) — e põe
as métricas lado a lado. A `leitura` compara CADA indicador em separado —
persistentes caírem enquanto comprometidos/aguardando SOBEM não é "redução",
é replay offline infiel — e imprime os quatro deltas. Para cada token que
termina `aguardando_resync`, `forense_pendentes` diz o último resync, se veio
book depois e o motivo; o fim da gravação NÃO conta como recuperação.

Não altera nada da política de integridade. Só MEDE.
"""

from __future__ import annotations

import argparse
import gzip
import json
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pulsearb.analysis.integrity import MAGNITUDE_CRITICA, MonitorDeIntegridade
from pulsearb.caminhos import caminho_de_escrita
from pulsearb.feeds.poly_ws import eventos_do_payload
from pulsearb.recorder.writer import FONTE_RESYNC
from pulsearb.replay.reader import ERROS_DE_FLUXO


def _motivo_comprometido(monitor: MonitorDeIntegridade, token: str) -> dict[str, Any]:
    """Por que ESTE token é `baixa` — a pergunta que o total não responde."""
    estado = monitor.estados[token]
    if not estado.teve_snapshot:
        motivo = "nunca_teve_snapshot"
    elif estado.magnitude_persistente_max > MAGNITUDE_CRITICA:
        motivo = "magnitude_persistente_critica"
    else:
        motivo = "fracao_de_tempo_ruim"
    return {
        "token": token,
        "motivo": motivo,
        "teve_snapshot": estado.teve_snapshot,
        "fracao_ruim": round(estado.fracao_ruim, 6),
        "ms_divergentes": round(estado.ms_divergentes, 1),
        "ms_sem_livro": round(estado.ms_sem_livro, 1),
        "ms_observados": round(estado.ms_observados, 1),
        "magnitude_persistente_max": round(estado.magnitude_persistente_max, 6),
        "divergencias_persistentes": estado.persistentes,
    }


@dataclass
class _ForenseDeResync:
    """Por token: quando foi o último `resync_book` e se um `book` veio depois.

    Existe para responder POR QUE um token termina `aguardando_resync`. O fim
    da gravação NÃO é recuperação: um token cujo último resync não teve book
    posterior fica pendente, e o motivo diz isso em vez de sumir da conta.
    """

    ultimo_resync_ns: dict[str, int] = field(default_factory=dict)
    book_apos_resync_ns: dict[str, int] = field(default_factory=dict)
    #: maior `ts_wall_ns` lido — o FIM da gravação. Diz, por pendente, quanto
    #: tempo a gravação ainda correu depois do resync: 50 ms é a gravação
    #: acabando no meio da recuperação; horas é token que não se recuperou.
    fim_ns: int = 0

    def resync(self, payload: Any, ts_registro_ns: int) -> None:
        if not isinstance(payload, dict):
            return
        ts_perda = payload.get("ts_perda_ns")
        if not isinstance(ts_perda, int) or isinstance(ts_perda, bool):
            ts_perda = ts_registro_ns  # marcador legado: só há o do registro
        for token in payload.get("tokens", []):
            if isinstance(token, str):
                self.ultimo_resync_ns[token] = ts_perda
                self.book_apos_resync_ns.pop(token, None)

    def evento(self, evento: dict[str, Any], ts_ns: int) -> None:
        if evento.get("event_type") != "book":
            return
        token = evento.get("asset_id")
        if not isinstance(token, str) or token in self.book_apos_resync_ns:
            return
        if token in self.ultimo_resync_ns and ts_ns >= self.ultimo_resync_ns[token]:
            self.book_apos_resync_ns[token] = ts_ns

    def pendentes(self, monitor: MonitorDeIntegridade) -> list[dict[str, Any]]:
        saida = []
        for token in sorted(monitor.aguardando_resync):
            ultimo = self.ultimo_resync_ns.get(token)
            posterior = self.book_apos_resync_ns.get(token)
            if ultimo is None:
                motivo = "sem_registro_de_resync_book"
            elif posterior is None:
                motivo = "sem_book_apos_o_ultimo_resync"
            else:
                motivo = "book_posterior_NAO_reancorou"
            saida.append(
                {
                    "token": token,
                    "ts_ultimo_resync_ns": ultimo,
                    "houve_book_posterior": posterior is not None,
                    "ts_book_posterior_ns": posterior,
                    "ms_do_resync_ao_fim_da_gravacao": (
                        None if ultimo is None else round((self.fim_ns - ultimo) / 1e6, 1)
                    ),
                    "motivo": motivo,
                }
            )
        return saida


def relatorio_de_diagnostico(
    monitor: MonitorDeIntegridade, forense: _ForenseDeResync | None = None
) -> dict[str, Any]:
    """As métricas que decidem, mais o porquê de cada token comprometido."""
    forense = forense or _ForenseDeResync()
    resumo = monitor.resumo()
    comprometidos = [
        _motivo_comprometido(monitor, token)
        for token in monitor.estados
        if monitor.token_corrompido(token)
    ]
    comprometidos.sort(key=lambda c: c["fracao_ruim"], reverse=True)
    persistentes = sum(e.persistentes for e in monitor.estados.values())
    return {
        "metricas": {
            "divergencias_alinhadas": resumo["divergencias"],
            "divergencias_transientes_ignoradas": (
                monitor.divergencias_transientes_ignoradas
            ),
            "resyncs_por_material": monitor.resyncs_por_material,
            "resyncs_por_persistencia": monitor.resyncs_por_persistencia,
            "divergencias_persistentes": persistentes,
            "tokens_comprometidos": len(comprometidos),
            "tokens_aguardando_resync": len(monitor.aguardando_resync),
            "snapshots_fora_de_ordem": resumo["alinhamento"][
                "snapshots_com_carimbo_fora_de_ordem"
            ],
            "marcadores_de_resync_legados": monitor.marcadores_de_resync_legados,
            "resyncs_ja_recuperados_no_replay": (
                monitor.resyncs_ja_recuperados_no_replay
            ),
            "observacoes_sem_snapshot": monitor.observacoes_sem_snapshot,
            "formas_de_price_change": resumo["formas_de_price_change"],
            "formas_de_book": resumo["snapshots_de_livro"]["formas"],
            "niveis_por_lado": resumo["snapshots_de_livro"]["niveis_por_lado"],
        },
        "motivos_dos_comprometidos": _contagem_de_motivos(comprometidos),
        "tokens_comprometidos": comprometidos[:50],
        "amostras_de_divergencia": resumo["amostras"][:20],
        "forense_pendentes": forense.pendentes(monitor),
    }


def _contagem_de_motivos(comprometidos: list[dict[str, Any]]) -> dict[str, int]:
    contagem: dict[str, int] = {}
    for c in comprometidos:
        contagem[c["motivo"]] = contagem.get(c["motivo"], 0) + 1
    return contagem


def diagnosticar(registros: Iterable[Any], *, replay_resync: bool) -> dict[str, Any]:
    """Roda o monitor sobre os registros. Núcleo PURO — o teste passa objetos
    com `.fonte`, `.payload`, `.ts_wall_ns`; o `main` passa `RecordingReader`.

    Com `replay_resync`, cada registro `resync_book` vira `marcar_perda` dos
    tokens dele — reproduzindo o que o recorder fez AO VIVO, para o diagnóstico
    medir se o descompasso de reprodução (item 4) explica os persistentes."""
    monitor = MonitorDeIntegridade()
    forense = _ForenseDeResync()
    for rec in registros:
        forense.fim_ns = max(forense.fim_ns, rec.ts_wall_ns)
        if _tratar_resync(monitor, rec, replay_resync, forense):
            continue
        if rec.fonte != "poly_ws":
            continue
        _observar_eventos(monitor, rec, forense)
    monitor.finalizar()
    return relatorio_de_diagnostico(monitor, forense)


def _tratar_resync(
    monitor: MonitorDeIntegridade, rec: Any, replay: bool, forense: _ForenseDeResync
) -> bool:
    """Reproduz uma perda do recorder e informa se o registro foi consumido.

    A reprodução é a função do monitor (`aplicar_marcador_de_resync`), a
    mesma para qualquer consumidor: ela respeita `ts_perda_ns` e não apaga um
    book de recuperação que o arquivo trouxe ANTES do marcador."""
    if rec.fonte != FONTE_RESYNC:
        return False
    if replay:
        monitor.aplicar_marcador_de_resync(rec.payload)
        forense.resync(rec.payload, rec.ts_wall_ns)
    return True


def _observar_eventos(
    monitor: MonitorDeIntegridade, rec: Any, forense: _ForenseDeResync
) -> None:
    for evento in eventos_do_payload(rec.payload):
        monitor.observar(evento, rec.ts_wall_ns)
        forense.evento(evento, rec.ts_wall_ns)


#: Arquivo modificado há menos que isto é tratado como AINDA EM GRAVAÇÃO.
QUIETO_PADRAO_S = 120.0

#: Código de saída quando a gravação lida não está íntegra (arquivo truncado,
#: linha corrompida) ou não há arquivo fechado para ler. O relatório sai
#: mesmo assim — mas ninguém o confunde com um diagnóstico limpo.
SAIDA_GRAVACAO_NAO_INTEGRA = 2


def _gzip_fechado(caminho: Path) -> bool:
    """O arquivo termina num trailer gzip válido (o writer o FECHOU)?

    Lê até o fim: é a única prova de que o fluxo terminou — o arquivo aberto
    e o que morreu no meio falham igual aqui. `.jsonl` sem compressão não tem
    trailer que prove nada: conta como não fechado (lado conservador)."""
    if caminho.suffix != ".gz":
        return False
    try:
        with gzip.open(caminho, "rb") as fluxo:
            while fluxo.read(1 << 20):
                pass
    except ERROS_DE_FLUXO:
        return False
    return True


def arquivos_da_gravacao(
    caminho: Path, *, agora: float, quieto_s: float = QUIETO_PADRAO_S
) -> tuple[list[Path], list[str]]:
    """Os arquivos a ler, e os excluídos por ainda estarem EM GRAVAÇÃO.

    O arquivo que o recorder está escrevendo não tem trailer gzip: lido, ele
    parece truncado, e o token com resync no fim dele pareceria preso. Num
    diretório, o arquivo modificado mais recentemente é excluído só se as DUAS
    coisas valem: foi tocado há menos de `quieto_s` E não termina num trailer
    gzip válido. Recência sozinha não prova nada — uma gravação que acabou de
    encerrar tem o último arquivo recente e FECHADO, com o relatório final
    dentro, e excluí-lo daria `integra: true` sem a última rotação (revisão do
    #199). Pelo mtime, e não pelo nome: `-1400-002` ordena ANTES de `-1400`
    por nome, mas é mais novo. Um arquivo passado explicitamente é lido — quem
    o escolheu sabe o que escolheu.
    """
    if caminho.is_file():
        return [caminho], []
    arquivos = sorted([*caminho.glob("*.jsonl.gz"), *caminho.glob("*.jsonl")])
    if not arquivos:
        return [], []
    mais_novo = max(arquivos, key=lambda a: a.stat().st_mtime)
    recente = agora - mais_novo.stat().st_mtime < quieto_s
    if recente and not _gzip_fechado(mais_novo):
        return [a for a in arquivos if a != mais_novo], [mais_novo.name]
    return arquivos, []


def _leitura_da_gravacao(
    reader: Any, lidos: list[Path], excluidos: list[str]
) -> dict[str, Any]:
    """Integridade do que foi LIDO, separada do que o replay concluiu.

    Um gzip truncado é defeito da GRAVAÇÃO, não do replay: sai aqui, com o
    arquivo e o erro, e não como token preso no relatório do monitor."""
    ilegiveis = list(reader.arquivos_ilegiveis)
    return {
        "arquivos_lidos": [a.name for a in lidos],
        "arquivos_em_gravacao_excluidos": excluidos,
        "arquivos_truncados_ou_ilegiveis": ilegiveis,
        "linhas_corrompidas": reader.corrompidas,
        "registros_fora_de_ordem": reader.fora_de_ordem,
        "integra": bool(lidos) and not ilegiveis and reader.corrompidas == 0,
    }


def _aviso_de_integridade(leitura: dict[str, Any]) -> str:
    if not leitura["arquivos_lidos"]:
        return "NENHUM arquivo fechado para ler: não há diagnóstico. "
    if leitura["integra"]:
        return ""
    return (
        "GRAVAÇÃO NÃO ÍNTEGRA ({n} arquivo(s) truncado(s)/ilegível(is), {c} "
        "linha(s) corrompida(s)): as métricas cobrem só o que foi lido, e um "
        "token pendente perto do ponto de quebra pode ser artefato da "
        "truncagem, não do replay. ".format(
            n=len(leitura["arquivos_truncados_ou_ilegiveis"]),
            c=leitura["linhas_corrompidas"],
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gravacao", help="arquivo .jsonl.gz ou diretório")
    parser.add_argument(
        "--json", default=None, help="grava o relatório comparativo neste caminho"
    )
    parser.add_argument(
        "--quieto-s",
        type=float,
        default=QUIETO_PADRAO_S,
        help="num diretório, o arquivo mais novo modificado há menos que isto "
        "é tratado como em gravação e excluído",
    )
    args = parser.parse_args(argv)

    # Import tardio: o script roda no Mac/VPS; o teste do núcleo não precisa
    # do reader nem de um arquivo.
    from pulsearb.replay.reader import RecordingReader

    caminho = Path(args.gravacao)
    lidos, excluidos = arquivos_da_gravacao(
        caminho, agora=time.time(), quieto_s=args.quieto_s
    )
    leitor = RecordingReader(lidos)
    sem = diagnosticar(leitor, replay_resync=False)
    leitura_da_gravacao = _leitura_da_gravacao(leitor, lidos, excluidos)
    com = diagnosticar(RecordingReader(lidos), replay_resync=True)
    comparativo = {
        "arquivo": str(caminho),
        "leitura_da_gravacao": leitura_da_gravacao,
        "sem_replay_de_resync": sem["metricas"],
        "com_replay_de_resync": com["metricas"],
        "leitura": _aviso_de_integridade(leitura_da_gravacao)
        + _leitura(sem["metricas"], com["metricas"]),
        "detalhe_sem_replay": _detalhe(sem),
        # O modo COM replay também é detalhado: sem isto, comprometidos que
        # SUBIRAM ao reproduzir os resyncs não tinham como ser examinados.
        "detalhe_com_replay": _detalhe(com),
    }
    saida = json.dumps(comparativo, indent=2, ensure_ascii=False)
    print(saida)
    if args.json:
        # `caminho_de_escrita` contém o destino à raiz permitida (S2083): um
        # `--json` não sanitizado é caminho de saída não confiável. Ver
        # `caminhos.py`.
        destino = caminho_de_escrita(args.json)
        destino.write_text(saida, encoding="utf-8")  # NOSONAR S2083
    if not leitura_da_gravacao["integra"]:
        return SAIDA_GRAVACAO_NAO_INTEGRA
    return 0


def _detalhe(diagnostico: dict[str, Any]) -> dict[str, Any]:
    return {
        "motivos_dos_comprometidos": diagnostico["motivos_dos_comprometidos"],
        "tokens_comprometidos": diagnostico["tokens_comprometidos"],
        "amostras_de_divergencia": diagnostico["amostras_de_divergencia"],
        "forense_pendentes": diagnostico["forense_pendentes"],
    }


def _fatos(sem: dict[str, Any], com: dict[str, Any]) -> str:
    """Os quatro indicadores, `sem→com`, sempre impressos junto do veredito."""
    return (
        "persistentes {ps}→{pc}, comprometidos {cs}→{cc}, aguardando_resync "
        "{as_}→{ac}, snapshots_fora_de_ordem {ss}→{sc}".format(
            ps=sem["divergencias_persistentes"], pc=com["divergencias_persistentes"],
            cs=sem["tokens_comprometidos"], cc=com["tokens_comprometidos"],
            as_=sem["tokens_aguardando_resync"], ac=com["tokens_aguardando_resync"],
            ss=sem["snapshots_fora_de_ordem"], sc=com["snapshots_fora_de_ordem"],
        )
    )


def _leitura(sem: dict[str, Any], com: dict[str, Any]) -> str:
    """Veredito FACTUAL, indicador a indicador — nunca diz que um número caiu
    quando subiu (a versão anterior usava um OR e afirmava redução de
    comprometidos quando eles AUMENTAVAM). Os quatro deltas vão impressos."""
    dp = com["divergencias_persistentes"] - sem["divergencias_persistentes"]
    dc = com["tokens_comprometidos"] - sem["tokens_comprometidos"]
    da = com["tokens_aguardando_resync"] - sem["tokens_aguardando_resync"]
    if dp > 0:
        veredito = (
            "Reproduzir os resyncs AUMENTA as divergências persistentes: o "
            "replay offline PIORA a reconstrução. Os números do modo SEM "
            "replay são os confiáveis."
        )
    elif dp < 0 and dc <= 0 and da <= 0:
        veredito = (
            "Reproduzir os resyncs REDUZ os persistentes SEM aumentar "
            "comprometidos nem aguardando_resync: o backtest não reproduzir os "
            "resyncs do recorder (mesmo caminho) explica os persistentes, e "
            "vale reproduzi-los."
        )
    elif dp < 0:
        veredito = (
            "Reproduzir os resyncs REDUZ os persistentes MAS AUMENTA "
            "comprometidos e/ou aguardando_resync: o replay offline NÃO é fiel "
            "— cria janelas sem-snapshot e tokens presos em recuperação que o "
            "recorder ao vivo não teve (a gravação termina no meio da "
            "recuperação). Confie no modo SEM replay; os persistentes de lá são "
            "o alvo. Ver `detalhe_com_replay.forense_pendentes`."
        )
    else:  # dp == 0
        veredito = (
            "Reproduzir os resyncs NÃO muda os persistentes. Investigue os do "
            "modo SEM replay (truncagem/perda): `tokens_comprometidos` e "
            "`niveis_por_lado` — topo afirmado abaixo do nível mais raso do "
            "snapshot = truncagem."
        )
    return f"{veredito} [{_fatos(sem, com)}]"


if __name__ == "__main__":
    raise SystemExit(main())
