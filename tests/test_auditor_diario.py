"""O auditor diário lê diários REAIS, e recusa o que não consegue apurar.

## Por que toda fixture daqui é um recorte de gravação de verdade

Este projeto já foi mordido DUAS vezes por fixture sintética que encodava a
suposição errada — `price_change` (§6.1b) e `market_resolved` (§12.13) — e nos
dois casos o parser lia ZERO eventos e o relatório saía normal. Um auditor com
esse defeito é pior que nenhum: ele assina "limpo" sobre um dia que não leu.

Por isso nenhum `.jsonl` daqui foi escrito à mão. Cada um é um recorte de
`data/shadow/`, linha por linha, com os campos como os dois produtores os
escrevem (`execution/cliente_sombra.py` para a rota maker, o portão de risco
para a decisão). A origem de cada arquivo de `tests/fixtures/diarios/`:

De `data/shadow/diario-20260924-044201-802197-41092.jsonl` (2 951 linhas):

- `limpo.jsonl` — 4 decisões liberadas do portão, em 4 janelas, mais 3
  cancelamentos da rota maker. As DUAS formas de linha no mesmo arquivo.
- `stake_acima_do_teto.jsonl` — 1 cotação maker de 100 × 0,11 = 11,00 USDC.
- `raspando_o_teto.jsonl` — 3 cotações maker de 100 × 0,05 = 5,00 USDC.
- `concentracao_numa_janela.jsonl` — 3 cotações da MESMA janela mais 2
  decisões de outras duas.
- `linha_corrompida.jsonl` — o `limpo` com a última linha truncada no meio,
  que é como um diário termina quando o processo morre escrevendo.
- `ordem_real_no_diario.jsonl` — uma cotação real com o prefixo do `order_id`
  trocado. É a única fixture com um byte alterado; ver o teste que a usa.

Dos outros diários reais:

- `mudanca_de_estrategia.jsonl` — duas rodadas do MESMO dia UTC
  (`diario-20260924-001851-404703-33034.jsonl`, tamanho 1000, e a de 044201,
  tamanho 100). Um dia tem várias rodadas; auditar o dia é somá-las.
- `churn_de_cotacoes.jsonl` — `diario-20260914-052154-115061-78026.jsonl`:
  1 decisão liberada e 4 cancelamentos de repouso 15 s.
- `disjuntor_armado.jsonl` — `diario-20260905-112249-725444-12085.jsonl`:
  4 recusas reais com motivo `disjuntor_armado`.

A ÚNICA fixture com um byte alterado é a última, e está anotada no teste que a
usa: `order_id` sem `sombra-` nunca aconteceu nos 31 diários (0 em 354 467
linhas), porque significaria ordem REAL. Não se pode recortar o que não
existe — e o alerta do RUNBOOK §10.1 precisa de teste de qualquer jeito.

## O registro do dia vem do próprio serializador

O diário é recortado; o registro é PRODUZIDO por `RegistroDoDia.como_dict()`,
que é o mesmo código que o portão usa para gravá-lo. Escrever o JSON do
registro à mão seria a volta da fixture que encoda a suposição, com a
agravante de que aqui existe um serializador para consultar. Dois testes leem
os arquivos REAIS de `data/risco/` quando eles estão no disco, para provar que
`de_dict` come a forma do Mac — e são pulados na sessão da nuvem, que não tem
`data/`.

## O que cada grupo cobre

- `TestOCaminhoLimpo`: sem alerta, saída 0, e as duas formas de linha LIDAS.
- `TestForaDaPolitica`: cada alerta de teto disparando, e nenhum teto cravado.
- `TestMudancaDeEstrategia` / `TestPerseguicaoDoObjetivo`: perguntas 4 e 5.
- `TestFalhaFechada`: cada recusa, com saída 2.
- `TestOFormato`: teto de 10 linhas e `ALERTA` como primeira palavra.
- `TestOsMotivosTemNome`: nenhum alerta anônimo sai do auditor.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from pulsearb.risk.gates import RegistroDoDia
from pulsearb.settings import FeedSettings, Mode, RiskSettings, Settings

RAIZ = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "diarios"


def _carregar(nome: str):
    """Importa um script de `scripts/` pelo caminho — não é pacote instalado."""
    spec = importlib.util.spec_from_file_location(nome, RAIZ / "scripts" / f"{nome}.py")
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[nome] = modulo
    spec.loader.exec_module(modulo)
    return modulo


auditor = _carregar("auditor_diario")
MOTIVOS = auditor.MOTIVOS_DO_AUDITOR


# ── o cenário: diário real copiado para onde a contenção de caminho exige ──


def _plantar(tmp_path: Path, fixture: str, *, nome: str = "diario.jsonl") -> str:
    """Copia a fixture para `data/shadow/` sob o cwd e devolve o arg relativo.

    `caminho_de_diario_lido` contém o argumento ao diretório de trabalho e às
    duas pastas de diário (S2083, `#206`). O teste obedece à contenção em vez
    de contorná-la: contornar aqui deixaria o caminho não exercitado.
    """
    pasta = tmp_path / "data" / "shadow"
    pasta.mkdir(parents=True, exist_ok=True)
    destino = pasta / nome
    destino.write_bytes((FIXTURES / fixture).read_bytes())
    return f"data/shadow/{nome}"


def _plantar_registro(tmp_path: Path, registro: RegistroDoDia) -> str:
    """Grava o registro do dia pelo serializador DELE, não à mão."""
    pasta = tmp_path / "data" / "risco"
    pasta.mkdir(parents=True, exist_ok=True)
    alvo = pasta / "registro_do_dia.json"
    alvo.write_text(
        json.dumps(registro.como_dict(), ensure_ascii=False), encoding="utf-8"
    )
    return "data/risco/registro_do_dia.json"


#: O registro de um dia em que nada estourou. Mesmo dia UTC das fixtures
#: recortadas do `diario-20260924-*`.
def _registro_limpo(dia: str = "2026-09-24") -> RegistroDoDia:
    return RegistroDoDia(
        dia=dia,
        gasto_por_janela={"btc-updown-5m-1790225100": 2.25},
        pnl_realizado_usdc=1.5,
    )


def _settings(**risco) -> Settings:
    return Settings(mode=Mode.SHADOW, feeds=FeedSettings(), risk=RiskSettings(**risco))


#: O dia UTC de que cada recorte saiu. O auditor RECUSA quando o registro é de
#: outro dia (ver `test_registro_de_OUTRO_dia_RECUSA`), então o registro que
#: acompanha cada fixture tem de ser do dia dela — e esta tabela é o que
#: impede o teste de "consertar" isso afrouxando a regra.
DIA_DA_FIXTURE = {
    "churn_de_cotacoes.jsonl": "2026-09-14",
    "disjuntor_armado.jsonl": "2026-09-05",
}
DIA_PADRAO = "2026-09-24"


def _auditar(tmp_path, monkeypatch, fixture, *, registro=None, settings=None, **extra):
    monkeypatch.chdir(tmp_path)
    diario = _plantar(tmp_path, fixture)
    dia = DIA_DA_FIXTURE.get(fixture, DIA_PADRAO)
    bruto = _plantar_registro(tmp_path, registro or _registro_limpo(dia))
    return auditor.auditar(
        diario=diario,
        registro_bruto=bruto,
        settings=settings or _settings(),
        **extra,
    )


# ────────────────────────────────────────────────────── o caminho limpo


class TestOCaminhoLimpo:
    def test_dia_dentro_da_politica_NAO_alerta_e_sai_zero(self, tmp_path, monkeypatch):
        relatorio = _auditar(tmp_path, monkeypatch, "limpo.jsonl")
        assert relatorio.alertas == {}, relatorio.alertas
        linhas = auditor.linhas_do_relatorio(relatorio)
        assert linhas[0].startswith("LIMPO")
        assert "ALERTA" not in linhas[0]

    def test_o_main_de_ponta_a_ponta_sai_zero(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        diario = _plantar(tmp_path, "limpo.jsonl")
        registro = _plantar_registro(tmp_path, _registro_limpo())
        (tmp_path / "config.yaml").write_text("mode: SHADOW\n", encoding="utf-8")
        codigo = auditor.main([diario, "--registro", registro, "--config", "config.yaml"])
        assert codigo == auditor.SAIDA_LIMPA
        assert capsys.readouterr().out.startswith("LIMPO auditor_diario dia=2026-09-24")

    def test_as_DUAS_formas_de_linha_sao_lidas_de_verdade(self, tmp_path, monkeypatch):
        """O defeito do §6.1b foi ler ZERO de uma forma e não notar.

        O `limpo` tem 4 decisões do portão e 3 cancelamentos da rota maker. Um
        leitor que enxergasse só uma das formas contaria 4 e 0, ou 0 e 3 — e o
        relatório sairia normal. Aqui as duas contagens são afirmadas.
        """
        relatorio = _auditar(tmp_path, monkeypatch, "limpo.jsonl")
        assert len(relatorio.leitura.todas) == 4
        assert {d.rota for d in relatorio.leitura.todas} == {auditor.ROTA_PORTAO}
        assert len(relatorio.leitura.repousos_s) == 3

    def test_a_rota_maker_e_lida_de_verdade(self, tmp_path, monkeypatch):
        """A outra metade do mesmo guarda: as linhas `evento` viram decisão."""
        relatorio = _auditar(
            tmp_path,
            monkeypatch,
            "raspando_o_teto.jsonl",
            settings=_settings(stake_max_por_trade_usdc=50.0),
        )
        assert len(relatorio.leitura.todas) == 3
        assert {d.rota for d in relatorio.leitura.todas} == {auditor.ROTA_MAKER}
        assert relatorio.movido_usdc == pytest.approx(15.0)

    def test_o_relatorio_responde_a_pergunta_1_com_numero(self, tmp_path, monkeypatch):
        relatorio = _auditar(tmp_path, monkeypatch, "limpo.jsonl")
        linha = auditor.linhas_do_relatorio(relatorio)[1]
        assert f"{relatorio.movido_usdc:.2f}" in linha
        assert f"{relatorio.risk.exposicao_max_usdc:.2f}" in linha

    def test_o_relatorio_responde_a_pergunta_2_com_motivo(self, tmp_path, monkeypatch):
        """As 3 maiores, e o motivo que o bot registrou em cada uma."""
        relatorio = _auditar(tmp_path, monkeypatch, "disjuntor_armado.jsonl")
        linha = next(
            ln for ln in auditor.linhas_do_relatorio(relatorio) if ln.startswith("maiores")
        )
        assert linha.count("#") == 3
        # O motivo é o que o PORTÃO gravou, não um texto do auditor.
        assert "disjuntor_armado" in linha


# ───────────────────────────────────────────── pergunta 3: fora da política


class TestForaDaPolitica:
    def test_stake_acima_do_teto(self, tmp_path, monkeypatch):
        """Cotação real de 100 × 0,11 = 11,00 USDC contra o teto de 5,00."""
        relatorio = _auditar(tmp_path, monkeypatch, "stake_acima_do_teto.jsonl")
        assert relatorio.alertas == {MOTIVOS.STAKE_ACIMA_DO_TETO: 1}

    def test_o_teto_do_stake_vem_do_config_e_nao_do_script(
        self, tmp_path, monkeypatch
    ):
        """O MESMO diário, com o teto subido, não alerta.

        É a trava contra número cravado: um teto escrito no auditor mente no
        dia em que alguém sobe o do `config.yaml` — e mente dizendo que está
        tudo bem.
        """
        relatorio = _auditar(
            tmp_path,
            monkeypatch,
            "stake_acima_do_teto.jsonl",
            settings=_settings(stake_max_por_trade_usdc=20.0),
        )
        assert relatorio.alertas == {}

    def test_preco_fora_da_faixa(self, tmp_path, monkeypatch):
        """A faixa aperta; os preços reais do dia passam a estar fora dela."""
        relatorio = _auditar(
            tmp_path,
            monkeypatch,
            "limpo.jsonl",
            settings=_settings(preco_minimo=0.90, preco_maximo=0.95),
        )
        assert MOTIVOS.PRECO_FORA_DA_FAIXA in relatorio.alertas

    def test_spread_acima_do_teto(self, tmp_path, monkeypatch):
        """O spread real do topo é de 1 tick (0,01); o teto cai abaixo dele."""
        relatorio = _auditar(
            tmp_path,
            monkeypatch,
            "limpo.jsonl",
            settings=_settings(spread_maximo=0.005),
        )
        assert relatorio.alertas == {MOTIVOS.SPREAD_ACIMA_DO_TETO: 4}

    def test_latencia_acima_do_teto(self, tmp_path, monkeypatch):
        """Nos 31 diários a `latencia_da_decisao_ms` real é SEMPRE 0,0.

        Não há recorte possível de uma latência alta porque ela nunca foi
        gravada. O que se prova aqui é o que se pode provar sem inventar
        linha: a comparação existe e lê o teto do `config.yaml`. Se a rodada
        passar a gravar latência de verdade, este teste continua válido e
        ganha um irmão com o número real.
        """
        relatorio = _auditar(
            tmp_path,
            monkeypatch,
            "limpo.jsonl",
            settings=_settings(atraso_max_ms=-1.0),
        )
        assert relatorio.alertas == {MOTIVOS.LATENCIA_ACIMA_DO_TETO: 4}

    def test_janela_acima_do_teto(self, tmp_path, monkeypatch):
        """11,00 USDC numa janela cujo teto é 10,00, com o teto por trade folgado."""
        relatorio = _auditar(
            tmp_path,
            monkeypatch,
            "stake_acima_do_teto.jsonl",
            settings=_settings(
                stake_max_por_trade_usdc=20.0, stake_max_por_janela_usdc=10.0
            ),
        )
        assert relatorio.alertas == {MOTIVOS.JANELA_ACIMA_DO_TETO: 1}

    def test_exposicao_acima_do_teto(self, tmp_path, monkeypatch):
        registro = RegistroDoDia(
            dia="2026-09-24", gasto_por_janela={"a": 30.0, "b": 25.0}
        )
        relatorio = _auditar(
            tmp_path, monkeypatch, "limpo.jsonl", registro=registro
        )
        assert relatorio.alertas == {MOTIVOS.EXPOSICAO_ACIMA_DO_TETO: 1}

    def test_posicoes_acima_do_teto(self, tmp_path, monkeypatch):
        registro = RegistroDoDia(
            dia="2026-09-24",
            gasto_por_janela={f"j{i}": 1.0 for i in range(6)},
        )
        relatorio = _auditar(
            tmp_path, monkeypatch, "limpo.jsonl", registro=registro
        )
        assert relatorio.alertas == {MOTIVOS.POSICOES_ACIMA_DO_TETO: 1}

    def test_perda_acima_do_teto(self, tmp_path, monkeypatch):
        registro = RegistroDoDia(dia="2026-09-24", pnl_realizado_usdc=-26.0)
        relatorio = _auditar(
            tmp_path, monkeypatch, "limpo.jsonl", registro=registro
        )
        assert relatorio.alertas == {MOTIVOS.PERDA_ACIMA_DO_TETO: 1}

    def test_disjuntor_armado(self, tmp_path, monkeypatch):
        """O disjuntor armado é alerta mesmo que nada mais tenha estourado.

        O bot deveria estar parado; o dia tem decisão registrada. Quem lê o
        relatório precisa ver isso na primeira linha.
        """
        registro = RegistroDoDia(dia="2026-09-05", disjuntor_armado=True)
        relatorio = _auditar(
            tmp_path, monkeypatch, "disjuntor_armado.jsonl", registro=registro
        )
        assert relatorio.alertas == {MOTIVOS.DISJUNTOR_ARMADO: 1}

    def test_pausa_em_curso(self, tmp_path, monkeypatch):
        """Pausa cujo fim é DEPOIS do fim da janela auditada."""
        registro = RegistroDoDia(dia="2026-09-24", pausado_ate_epoch=1e12)
        relatorio = _auditar(
            tmp_path, monkeypatch, "limpo.jsonl", registro=registro
        )
        assert relatorio.alertas == {MOTIVOS.PAUSA_EM_CURSO: 1}

    def test_a_pausa_e_julgada_contra_o_dia_auditado(self, tmp_path, monkeypatch):
        """Pausa que já tinha terminado no dia auditado NÃO alerta.

        Julgá-la contra `time.time()` faria toda pausa antiga parecer ativa, e
        o auditor de qualquer dia passado sairia sempre alertando.
        """
        registro = RegistroDoDia(dia="2026-09-24", pausado_ate_epoch=1.0)
        relatorio = _auditar(
            tmp_path, monkeypatch, "limpo.jsonl", registro=registro
        )
        assert relatorio.alertas == {}

    def test_ordem_real_no_diario(self, tmp_path, monkeypatch):
        """RUNBOOK §10.1: `order_id` sem `sombra-` significaria ordem REAL.

        É a única fixture daqui com um byte alterado, e por um motivo que é o
        próprio achado: nos 31 diários reais não existe um `order_id` sem o
        prefixo (0 em 354 467 linhas). Não se recorta o que nunca aconteceu —
        e a instrução do RUNBOOK para quando acontecer é parar o serviço, o
        que exige que o alerta exista antes.
        """
        relatorio = _auditar(
            tmp_path,
            monkeypatch,
            "ordem_real_no_diario.jsonl",
            settings=_settings(
                stake_max_por_trade_usdc=100.0, stake_max_por_janela_usdc=1000.0
            ),
        )
        assert relatorio.alertas == {MOTIVOS.ORDEM_REAL_NO_DIARIO: 1}
        assert relatorio.ids_sem_prefixo_de_sombra


# ──────────────────────────────────────────── pergunta 4: mudou de estratégia


class TestMudancaDeEstrategia:
    def test_dois_tamanhos_no_dia_com_estouro_de_teto_ALERTA(
        self, tmp_path, monkeypatch
    ):
        """Duas rodadas reais do MESMO dia UTC, cotando 1000 e 100 shares.

        Nenhuma das duas cabia em `stake_max_por_trade_usdc`: a mudança
        aconteceu e não era permitida.
        """
        relatorio = _auditar(tmp_path, monkeypatch, "mudanca_de_estrategia.jsonl")
        assert relatorio.estrategia.mudou
        assert relatorio.estrategia.tamanhos_por_rota[auditor.ROTA_MAKER] == [
            100.0,
            1000.0,
        ]
        assert MOTIVOS.MUDANCA_DE_ESTRATEGIA_NAO_PERMITIDA in relatorio.alertas

    def test_mesma_mudanca_dentro_do_teto_e_PERMITIDA(self, tmp_path, monkeypatch):
        """A mudança continua registrada; o que muda é o veredito dela.

        "Mudou" e "não podia mudar" são perguntas diferentes, e o relatório
        responde as duas em separado.
        """
        relatorio = _auditar(
            tmp_path,
            monkeypatch,
            "mudanca_de_estrategia.jsonl",
            settings=_settings(
                stake_max_por_trade_usdc=1000.0, stake_max_por_janela_usdc=10_000.0
            ),
        )
        assert relatorio.estrategia.mudou
        assert MOTIVOS.MUDANCA_DE_ESTRATEGIA_NAO_PERMITIDA not in relatorio.alertas
        linha = next(
            ln
            for ln in auditor.linhas_do_relatorio(relatorio)
            if ln.startswith("estrategia")
        )
        assert "mudou=sim" in linha and "permitida=sim" in linha

    def test_um_tamanho_so_NAO_e_mudanca(self, tmp_path, monkeypatch):
        relatorio = _auditar(tmp_path, monkeypatch, "limpo.jsonl")
        assert not relatorio.estrategia.mudou


# ─────────────────────────────── pergunta 5: perseguição indesejada do objetivo


class TestPerseguicaoDoObjetivo:
    def test_raspando_o_teto_repetidamente(self, tmp_path, monkeypatch):
        """3 cotações reais de exatamente 5,00 USDC — o teto, não acima dele.

        Nenhuma estoura o teto por trade (5,00 não é maior que 5,00), e é
        justamente esse o sinal: o agente não violou nada e ainda assim mirou
        o limite três vezes seguidas.
        """
        relatorio = _auditar(tmp_path, monkeypatch, "raspando_o_teto.jsonl")
        assert relatorio.alertas == {MOTIVOS.RASPANDO_O_TETO: 3}
        assert MOTIVOS.STAKE_ACIMA_DO_TETO not in relatorio.alertas

    def test_uma_raspagem_sozinha_NAO_alerta(self, tmp_path, monkeypatch):
        """Uma é o tamanho que deu; `RASPAGENS_PARA_ALERTA` é o que faz padrão."""
        relatorio = _auditar(
            tmp_path,
            monkeypatch,
            "stake_acima_do_teto.jsonl",
            settings=_settings(stake_max_por_trade_usdc=20.0),
        )
        assert MOTIVOS.RASPANDO_O_TETO not in relatorio.alertas

    def test_concentracao_numa_janela(self, tmp_path, monkeypatch):
        """3 janelas no dia, e uma delas com mais de 60 % do movido."""
        relatorio = _auditar(
            tmp_path,
            monkeypatch,
            "concentracao_numa_janela.jsonl",
            settings=_settings(
                stake_max_por_trade_usdc=100.0, stake_max_por_janela_usdc=1000.0
            ),
        )
        assert relatorio.alertas == {MOTIVOS.CONCENTRACAO_NUMA_JANELA: 1}
        assert relatorio.perseguicao.janelas == 3
        assert relatorio.perseguicao.fracao_da_maior_janela > 0.60

    def test_dia_espalhado_NAO_alerta_concentracao(self, tmp_path, monkeypatch):
        relatorio = _auditar(tmp_path, monkeypatch, "limpo.jsonl")
        assert relatorio.perseguicao.janelas == 4
        assert MOTIVOS.CONCENTRACAO_NUMA_JANELA not in relatorio.alertas

    def test_churn_de_cotacoes(self, tmp_path, monkeypatch):
        """4 cancelamentos reais de 15 s contra um repouso mínimo de 20 s.

        O repouso mínimo é sensibilidade do auditor, não teto de risco: vem da
        linha de comando. Nos diários reais o repouso mais curto é de 15,004 s
        — abaixo de 10 s (o default) não existe amostra, e inventar uma seria
        a fixture sintética de novo.
        """
        relatorio = _auditar(
            tmp_path,
            monkeypatch,
            "churn_de_cotacoes.jsonl",
            repouso_minimo_s=20.0,
        )
        assert relatorio.alertas == {MOTIVOS.CHURN_DE_COTACOES: 1}
        assert relatorio.perseguicao.churn == 1.0

    def test_repouso_normal_NAO_alerta_churn(self, tmp_path, monkeypatch):
        relatorio = _auditar(tmp_path, monkeypatch, "churn_de_cotacoes.jsonl")
        assert relatorio.perseguicao.churn == 0.0
        assert MOTIVOS.CHURN_DE_COTACOES not in relatorio.alertas


# ───────────────────────────────────────────────────────── falha fechada


class TestFalhaFechada:
    def _recusa(self, tmp_path, monkeypatch, **kwargs) -> str:
        monkeypatch.chdir(tmp_path)
        with pytest.raises(auditor.RecusaDoAuditor) as erro:
            auditor.auditar(settings=_settings(), **kwargs)
        return erro.value.motivo

    def test_diario_ausente_RECUSA(self, tmp_path, monkeypatch):
        (tmp_path / "data" / "shadow").mkdir(parents=True)
        motivo = self._recusa(
            tmp_path,
            monkeypatch,
            diario="data/shadow/nao-existe.jsonl",
            registro_bruto=_plantar_registro(tmp_path, _registro_limpo()),
        )
        assert motivo == MOTIVOS.RECUSA_DIARIO_AUSENTE

    def test_diario_fora_da_pasta_permitida_RECUSA(self, tmp_path, monkeypatch):
        """A travessia de caminho do `#206` (S2083) não volta por aqui."""
        (tmp_path / "outra").mkdir()
        (tmp_path / "outra" / "x.jsonl").write_text("{}\n", encoding="utf-8")
        motivo = self._recusa(
            tmp_path,
            monkeypatch,
            diario="../../../etc/passwd.jsonl",
            registro_bruto=_plantar_registro(tmp_path, _registro_limpo()),
        )
        assert motivo == MOTIVOS.RECUSA_DIARIO_ILEGIVEL

    def test_linha_corrompida_RECUSA(self, tmp_path, monkeypatch):
        """O diário limpo com a última linha truncada — o processo morreu escrevendo.

        O leitor do resumo da rodada PULA linha ilegível de propósito (o
        `journalctl` intercala as dele). O auditor não pode: a linha que ele
        não leu é justamente a decisão que ele não auditou, e dizer "limpo"
        depois disso é a falha aberta.
        """
        monkeypatch.chdir(tmp_path)
        motivo = self._recusa(
            tmp_path,
            monkeypatch,
            diario=_plantar(tmp_path, "linha_corrompida.jsonl"),
            registro_bruto=_plantar_registro(tmp_path, _registro_limpo()),
        )
        assert motivo == MOTIVOS.RECUSA_LINHA_CORROMPIDA

    def test_linha_sem_as_chaves_da_decisao_RECUSA(self, tmp_path, monkeypatch):
        """Forma nova do produtor é RECUSA, não zero decisões lidas.

        É literalmente o defeito do `price_change` (§6.1b): o parser deixou de
        achar o campo e o relatório saiu normal. Aqui a decisão real perde UM
        campo e o auditor para.
        """
        monkeypatch.chdir(tmp_path)
        bruto = (FIXTURES / "limpo.jsonl").read_text(encoding="utf-8").splitlines()
        primeira = json.loads(bruto[0])
        del primeira["melhor_bid"]
        pasta = tmp_path / "data" / "shadow"
        pasta.mkdir(parents=True)
        (pasta / "d.jsonl").write_text(
            json.dumps(primeira) + "\n", encoding="utf-8"
        )
        motivo = self._recusa(
            tmp_path,
            monkeypatch,
            diario="data/shadow/d.jsonl",
            registro_bruto=_plantar_registro(tmp_path, _registro_limpo()),
        )
        assert motivo == MOTIVOS.RECUSA_LINHA_CORROMPIDA

    def test_evento_desconhecido_RECUSA(self, tmp_path, monkeypatch):
        """Evento que o auditor não conhece é decisão que ele não contou."""
        monkeypatch.chdir(tmp_path)
        pasta = tmp_path / "data" / "shadow"
        pasta.mkdir(parents=True)
        (pasta / "d.jsonl").write_text(
            json.dumps({"evento": "cotacao_preenchida", "ts": 1.0}) + "\n",
            encoding="utf-8",
        )
        motivo = self._recusa(
            tmp_path,
            monkeypatch,
            diario="data/shadow/d.jsonl",
            registro_bruto=_plantar_registro(tmp_path, _registro_limpo()),
        )
        assert motivo == MOTIVOS.RECUSA_LINHA_CORROMPIDA

    def test_registro_ausente_RECUSA(self, tmp_path, monkeypatch):
        """Sem o registro não se sabe do disjuntor nem do PnL — e não saber recusa."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data" / "risco").mkdir(parents=True)
        motivo = self._recusa(
            tmp_path,
            monkeypatch,
            diario=_plantar(tmp_path, "limpo.jsonl"),
            registro_bruto="data/risco/registro_do_dia.json",
        )
        assert motivo == MOTIVOS.RECUSA_REGISTRO_AUSENTE

    def test_registro_ilegivel_RECUSA(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        pasta = tmp_path / "data" / "risco"
        pasta.mkdir(parents=True)
        (pasta / "registro_do_dia.json").write_text("{nao e json", encoding="utf-8")
        motivo = self._recusa(
            tmp_path,
            monkeypatch,
            diario=_plantar(tmp_path, "limpo.jsonl"),
            registro_bruto="data/risco/registro_do_dia.json",
        )
        assert motivo == MOTIVOS.RECUSA_REGISTRO_ILEGIVEL

    def test_registro_sem_o_dia_RECUSA(self, tmp_path, monkeypatch):
        """`de_dict` exige `dia`; um registro sem ele não diz de que dia é."""
        monkeypatch.chdir(tmp_path)
        pasta = tmp_path / "data" / "risco"
        pasta.mkdir(parents=True)
        (pasta / "registro_do_dia.json").write_text("{}", encoding="utf-8")
        motivo = self._recusa(
            tmp_path,
            monkeypatch,
            diario=_plantar(tmp_path, "limpo.jsonl"),
            registro_bruto="data/risco/registro_do_dia.json",
        )
        assert motivo == MOTIVOS.RECUSA_REGISTRO_ILEGIVEL

    def test_registro_de_OUTRO_dia_RECUSA(self, tmp_path, monkeypatch):
        """Números de um dia com decisões de outro sairia coerente e errado."""
        monkeypatch.chdir(tmp_path)
        motivo = self._recusa(
            tmp_path,
            monkeypatch,
            diario=_plantar(tmp_path, "limpo.jsonl"),
            registro_bruto=_plantar_registro(tmp_path, _registro_limpo("2026-01-01")),
        )
        assert motivo == MOTIVOS.RECUSA_DIA_INDETERMINADO

    def test_janela_sem_decisao_RECUSA(self, tmp_path, monkeypatch):
        """Zero decisões na janela não é um dia limpo: é um dia não apurado.

        É o caso que o relógio de parede produziria sobre um diário guardado —
        e o relatório sairia dizendo 0,00 USDC movido, sem alerta nenhum.
        """
        monkeypatch.chdir(tmp_path)
        motivo = self._recusa(
            tmp_path,
            monkeypatch,
            diario=_plantar(tmp_path, "limpo.jsonl"),
            registro_bruto=_plantar_registro(tmp_path, _registro_limpo()),
            fim_s=4e9,
        )
        assert motivo == MOTIVOS.RECUSA_DIA_INDETERMINADO

    def test_diario_vazio_RECUSA(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        pasta = tmp_path / "data" / "shadow"
        pasta.mkdir(parents=True)
        (pasta / "d.jsonl").write_text("\n\n", encoding="utf-8")
        motivo = self._recusa(
            tmp_path,
            monkeypatch,
            diario="data/shadow/d.jsonl",
            registro_bruto=_plantar_registro(tmp_path, _registro_limpo()),
        )
        assert motivo == MOTIVOS.RECUSA_DIA_INDETERMINADO

    def test_a_recusa_sai_como_ALERTA_e_codigo_2(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data" / "shadow").mkdir(parents=True)
        (tmp_path / "config.yaml").write_text("mode: SHADOW\n", encoding="utf-8")
        codigo = auditor.main(
            ["data/shadow/nao-existe.jsonl", "--config", "config.yaml"]
        )
        assert codigo == auditor.SAIDA_RECUSA
        saida = capsys.readouterr().out.splitlines()
        assert saida[0].split()[0] == "ALERTA"
        assert MOTIVOS.RECUSA_DIARIO_AUSENTE in saida[0]

    def test_config_ilegivel_RECUSA_com_nome(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        diario = _plantar(tmp_path, "limpo.jsonl")
        (tmp_path / "config.yaml").write_text("risk: [nao e mapa]\n", encoding="utf-8")
        codigo = auditor.main([diario, "--config", "config.yaml"])
        assert codigo == auditor.SAIDA_RECUSA
        assert MOTIVOS.RECUSA_POLITICA_ILEGIVEL in capsys.readouterr().out


# ───────────────────────────────────────────────────────────── o formato


class TestOFormato:
    @pytest.mark.parametrize(
        "fixture",
        [
            "limpo.jsonl",
            "stake_acima_do_teto.jsonl",
            "raspando_o_teto.jsonl",
            "mudanca_de_estrategia.jsonl",
            "disjuntor_armado.jsonl",
        ],
    )
    def test_o_relatorio_cabe_em_dez_linhas(self, tmp_path, monkeypatch, fixture):
        relatorio = _auditar(tmp_path, monkeypatch, fixture)
        linhas = auditor.linhas_do_relatorio(relatorio)
        assert len(linhas) <= auditor.MAX_LINHAS_DO_RELATORIO == 10
        assert all("\n" not in linha for linha in linhas)

    def test_ALERTA_e_a_PRIMEIRA_palavra_quando_ha_alerta(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.chdir(tmp_path)
        diario = _plantar(tmp_path, "stake_acima_do_teto.jsonl")
        registro = _plantar_registro(tmp_path, _registro_limpo())
        (tmp_path / "config.yaml").write_text("mode: SHADOW\n", encoding="utf-8")
        codigo = auditor.main(
            [diario, "--registro", registro, "--config", "config.yaml"]
        )
        assert codigo == auditor.SAIDA_ALERTA
        saida = capsys.readouterr().out.splitlines()
        assert saida[0].split()[0] == "ALERTA"
        assert len(saida) <= auditor.MAX_LINHAS_DO_RELATORIO

    def test_as_linhas_da_recusa_tambem_cabem(self):
        recusa = auditor.RecusaDoAuditor(
            MOTIVOS.RECUSA_LINHA_CORROMPIDA, {"linha": 7}
        )
        linhas = auditor.linhas_da_recusa(recusa)
        assert len(linhas) <= auditor.MAX_LINHAS_DO_RELATORIO
        assert linhas[0].split()[0] == "ALERTA"


# ───────────────────────────────────────────── nenhum alerta é anônimo


class TestOsMotivosTemNome:
    def test_TODOS_e_congelado_e_cobre_alertas_e_recusas(self):
        assert isinstance(MOTIVOS.TODOS, frozenset)
        assert MOTIVOS.TODOS == MOTIVOS.ALERTAS | MOTIVOS.RECUSAS
        assert not MOTIVOS.ALERTAS & MOTIVOS.RECUSAS

    def test_toda_constante_publica_esta_em_TODOS(self):
        """A lista não pode ficar atrás da classe.

        Um motivo declarado e esquecido no `TODOS` é um alerta que o
        `validar_motivo` recusa — descobrir isso em produção, às 6 da manhã,
        é descobrir tarde.
        """
        declarados = {
            valor
            for nome, valor in vars(MOTIVOS).items()
            if nome.isupper() and isinstance(valor, str)
        }
        assert declarados == MOTIVOS.TODOS

    def test_alerta_sem_nome_e_RECUSADO(self):
        with pytest.raises(ValueError, match="motivo de auditoria desconhecido"):
            auditor.validar_motivo("o bot está estranho hoje")

    def test_recusa_nao_aceita_nome_de_alerta(self):
        with pytest.raises(ValueError, match="é alerta, não recusa"):
            auditor.RecusaDoAuditor(MOTIVOS.DISJUNTOR_ARMADO)

    @pytest.mark.parametrize(
        "fixture", ["limpo.jsonl", "mudanca_de_estrategia.jsonl", "raspando_o_teto.jsonl"]
    )
    def test_todo_alerta_emitido_tem_nome_conhecido(
        self, tmp_path, monkeypatch, fixture
    ):
        relatorio = _auditar(tmp_path, monkeypatch, fixture)
        assert set(relatorio.alertas) <= MOTIVOS.ALERTAS


# ──────────────────── o registro REAL do Mac, quando ele está no disco


REGISTRO_REAL = RAIZ / "data" / "risco" / "registro_do_dia.shadow.json"


@pytest.mark.skipif(
    not REGISTRO_REAL.is_file(),
    reason="a sessão da nuvem não tem data/; o Mac tem",
)
class TestOArquivoRealDoMac:
    def test_de_dict_come_o_registro_REAL_sem_adaptacao(self):
        """A forma do arquivo que está no disco, não a que imaginamos."""
        dado = json.loads(REGISTRO_REAL.read_text(encoding="utf-8"))
        registro = RegistroDoDia.de_dict(dado)
        assert registro.dia == dado["dia"]
        assert registro.exposicao_total_usdc == pytest.approx(
            sum(dado["gasto_por_janela"].values())
        )

    def test_o_auditor_roda_sobre_o_diario_REAL_de_2951_linhas(self, monkeypatch):
        """Ponta a ponta sobre o arquivo de verdade, com a política de verdade.

        O que se afirma é que ele APURA — não que o dia estava limpo. Ele não
        estava: a rota maker cotou 100 shares a 0,35, muito acima do teto por
        trade de 5,00 USDC.
        """
        diario = "data/shadow/diario-20260924-044201-802197-41092.jsonl"
        if not (RAIZ / diario).is_file():
            pytest.skip("diário real ausente")
        # A contenção de `caminho_de_diario_lido` é relativa ao cwd, e o teste
        # não depende de onde o pytest foi invocado.
        monkeypatch.chdir(RAIZ)
        relatorio = auditor.auditar(
            diario=diario,
            registro_bruto="data/risco/registro_do_dia.shadow.json",
            settings=Settings.load(RAIZ / "config.yaml"),
        )
        assert relatorio.dia == "2026-09-24"
        assert MOTIVOS.STAKE_ACIMA_DO_TETO in relatorio.alertas
        assert len(auditor.linhas_do_relatorio(relatorio)) <= 10
