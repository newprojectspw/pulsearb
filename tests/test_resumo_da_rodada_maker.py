"""O leitor da rodada do 4.2 lê campos que EXISTEM, e recusa o que não mede.

## O defeito que a primeira metade destes testes impede

Aconteceu duas vezes no projeto, e as duas passaram por `ruff` e por revisão:

1. O `#96` pôs `fecha_no_pior_caso` no JSON e o `resumo_m2.py` continuou lendo
   só `o_que_falta_para_fechar`. Um item que podia ser ❌ ficou ⬜ **em toda
   rodada**.
2. A correção disso foi escrita com o caminho **adivinhado** e a saída ficou
   byte a byte idêntica à de antes.

Comparar strings não pega nenhum dos dois. O que pega é **percorrer** o
caminho dentro de um `estado()` que o `ProcessoShadow` de verdade acabou de
produzir — rename de qualquer lado quebra o teste em vez de virar veredito
ausente.

## E a segunda metade: as quatro maneiras de perder 14 dias

Rodada confundida, regras trocadas no meio, rodada dormindo e laço que não
subiu. Cada uma tem de virar RECUSA COM NOME sobre um relato realista, porque
a que não vira é a que alguém lê como resultado.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from pulsearb.live.ciclo import CicloAoVivo
from pulsearb.live.shadow import ProcessoShadow, montar_ciclo
from pulsearb.settings import FeedSettings, Mode, RiskSettings, Settings

RAIZ = Path(__file__).resolve().parents[1]


def _carregar(nome: str):
    """Importa um script de `scripts/` pelo caminho — não é pacote instalado."""
    spec = importlib.util.spec_from_file_location(nome, RAIZ / "scripts" / f"{nome}.py")
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[nome] = modulo
    spec.loader.exec_module(modulo)
    return modulo


resumo = _carregar("resumo_da_rodada_maker")


# ── o estado de verdade, produzido pelo produtor ─────────────────────────
def _settings(tmp_path: Path) -> Settings:
    return Settings(
        mode=Mode.SHADOW,
        feeds=FeedSettings(),
        risk=RiskSettings(
            caminho_do_registro=str(tmp_path / "registro.json"),
            caminho_do_kill=str(tmp_path / "KILL"),
        ),
    )


def _estado_de_verdade(tmp_path: Path) -> dict:
    """Um relato como o processo o emite — não escrito à mão.

    Fixture sintética é justamente o que deixou passar os dois defeitos
    silenciosos do `price_change` e do `market_resolved`: ela encoda o que se
    imagina que o produtor manda. Aqui o produtor produz.
    """
    diario = tmp_path / "diario.jsonl"
    ciclo: CicloAoVivo = montar_ciclo(_settings(tmp_path), caminho_do_diario=diario)
    processo = ProcessoShadow(_settings(tmp_path), ciclo, caminho_do_diario=diario)
    assert processo.laco_maker is not None, (
        "sem laço maker o estado não traz `maker` e o teste de caminhos não "
        "prova nada — é o próprio caso `relato_sem_maker`"
    )
    return {"msg": resumo.MSG_DO_RELATO, **processo.estado()}


CAMINHOS = [
    resumo.CAMPO_DO_MAKER,
    resumo.CAMPO_DAS_REGRAS,
    resumo.CAMPO_DOS_MOTIVOS,
    resumo.CAMPO_DAS_REPOUSANDO,
    resumo.CAMPO_DO_LIQUIDO,
    resumo.CAMPO_DOS_REWARDS,
    resumo.CAMPO_DO_MARKOUT_USDC,
    resumo.CAMPO_DO_MARKOUT_CS,
    resumo.CAMPO_DAS_MEDIDAS,
    resumo.CAMPO_DO_REPOUSO_S,
    resumo.CAMPO_DAS_ATRAVESSADAS,
    resumo.CAMPO_DAS_NO_NIVEL,
    resumo.CAMPO_DOS_REENVIADOS,
    resumo.CAMPO_DO_CICLO,
    resumo.CAMPO_DA_PAREDE,
    resumo.CAMPO_DO_SONO,
]


class TestOsCamposExistem:
    @pytest.mark.parametrize("caminho", CAMINHOS)
    def test_o_campo_que_o_leitor_le_EXISTE_no_relato(self, caminho, tmp_path):
        """Percorrido, não comparado. Rename dos dois lados quebra aqui."""
        atual = _estado_de_verdade(tmp_path)
        for passo in caminho.split("."):
            assert isinstance(atual, dict), f"{caminho}: {passo} não é dicionário"
            assert passo in atual, f"{caminho}: falta o degrau {passo!r}"
            atual = atual[passo]

    def test_o_campo_do_falhou_sai_no_relato_inclusive_como_None(self, tmp_path):
        """`falhou` é a recusa `processo_falhou`, e só serve se sair SEMPRE.

        Um campo que só aparece quando há erro é um campo que ninguém procura
        quando não há — está escrito assim no `estado()`.
        """
        assert resumo.CAMPO_DO_FALHOU in _estado_de_verdade(tmp_path)

    def test_as_tres_regras_do_leitor_sao_as_tres_do_motor(self, tmp_path):
        """Uma quarta regra no motor sem entrar aqui sairia como rodada-base.

        É o caso mais provável do futuro: o próximo knob experimental nasce
        em `LacoMaker.resumo()` e ninguém lembra do leitor. A rodada dele
        seria lida como BASE e o quadro registraria a medida errada.
        """
        regras = _estado_de_verdade(tmp_path)["maker"]["regras"]
        assert set(regras) == set(resumo.REGRAS_EXPERIMENTAIS)


# ── relatos sintéticos, para os desfechos ────────────────────────────────
#: A partir daqui as fixtures são montadas: o que se testa é a DECISÃO sobre
#: um relato, e produzir 14 dias de rodada num teste não é possível. O que
#: garante que a forma é a real são os testes de cima.
def _relato(**mudancas) -> dict:
    regras = {
        "recolhe_quando_o_livro_anda": False,
        "ticks_abaixo_do_microprice": None,
        "pausa_apos_fill_toxico_s": None,
    }
    regras.update(mudancas.pop("regras", {}))
    base = {
        "msg": resumo.MSG_DO_RELATO,
        "falhou": None,
        "vigilia": {
            "da_rodada": {
                "parede_s": resumo.PAREDE_EXIGIDA_S + 60,
                "acordado_s": resumo.PAREDE_EXIGIDA_S + 60,
                "dormiu_s": 0.0,
                "ciclo_de_trabalho": 1.0,
            }
        },
        "maker": {
            "cotacoes_repousando": 3,
            "regras": regras,
            "motivos": {"repousada": 900, "manter": 120},
            "caixa": {
                "rewards_pro_rata_usdc": 200.0,
                "segundos_repousando": 100000.0,
                "liquido_pro_rata_usdc": 150.0,
                "execucoes_possiveis": {
                    "atravessadas": 12, "no_nivel": 40, "reenviados": 3
                },
                "markout": {
                    "medidas": 52, "centavos_por_share": -0.1974,
                    "resultado_usdc": -50.0,
                },
            },
        },
    }
    for chave, valor in mudancas.items():
        base[chave] = valor
    return base


class TestOVeredito:
    def test_rodada_completa_com_liquido_positivo_PASSA(self):
        veredito, motivo, _ = resumo._julgar([_relato()])
        assert (veredito, motivo) == (resumo.PASSA, None)

    def test_rodada_completa_com_liquido_negativo_REPROVA(self):
        """REPROVA é resultado, não falha: é o item medido e vencido."""
        relato = _relato()
        relato["maker"]["caixa"]["liquido_pro_rata_usdc"] = -3.0
        veredito, motivo, _ = resumo._julgar([relato])
        assert (veredito, motivo) == (resumo.REPROVA, None)

    def test_liquido_exatamente_zero_REPROVA(self):
        """Zero não é edge. `> 0`, não `>= 0`."""
        relato = _relato()
        relato["maker"]["caixa"]["liquido_pro_rata_usdc"] = 0.0
        veredito, _, _ = resumo._julgar([relato])
        assert veredito == resumo.REPROVA

    def test_arquivo_sem_relato_nenhum_RECUSA_com_nome(self):
        assert resumo._julgar([]) [:2] == (resumo.NAO_AVALIAVEL, "sem_relato")


class TestAsQuatroManeirasDePerderQuatorzeDias:
    def test_laco_maker_que_nao_subiu_RECUSA(self):
        """`laco_de_cotacao` volta com "sem caminho de diario" e a rodada segue.

        Relatando, viva, medindo nada. Sem esta recusa o líquido ausente
        viraria `campo_ausente`, que manda conferir versão do motor — pista
        errada para um serviço que subiu sem diário.
        """
        relato = _relato()
        relato["maker"] = None
        assert resumo._julgar([relato])[:2] == (
            resumo.NAO_AVALIAVEL, "relato_sem_maker"
        )

    def test_duas_regras_ligadas_na_mesma_rodada_RECUSA(self):
        relato = _relato(
            regras={"recolhe_quando_o_livro_anda": True, "pausa_apos_fill_toxico_s": 30.0}
        )
        assert resumo._julgar([relato])[:2] == (
            resumo.NAO_AVALIAVEL, "rodada_confundida"
        )

    def test_regras_trocadas_no_MEIO_da_rodada_RECUSA(self):
        """A unit anexa ao mesmo diário: um restart continua a rodada.

        Editar a unit e reiniciar mistura duas regras nos mesmos 14 dias e
        não deixa rastro nenhum — o último relato sozinho mostra UMA regra
        ligada e pareceria uma rodada limpa.
        """
        primeiro = _relato(regras={"recolhe_quando_o_livro_anda": True})
        depois = _relato(regras={"pausa_apos_fill_toxico_s": 30.0})
        assert resumo._julgar([primeiro, depois])[:2] == (
            resumo.NAO_AVALIAVEL, "regras_mudaram_no_meio"
        )

    def test_rodada_que_dormiu_RECUSA_mesmo_com_liquido_positivo(self):
        """3.16: 24 h com ciclo 0,12 observaram 2,9 h de mercado."""
        relato = _relato()
        relato["vigilia"]["da_rodada"]["ciclo_de_trabalho"] = 0.12
        assert resumo._julgar([relato])[:2] == (
            resumo.NAO_AVALIAVEL, "rodada_dormiu"
        )

    def test_processo_que_falhou_RECUSA(self):
        assert resumo._julgar([_relato(falhou="io_do_diario_maker: disco cheio")])[:2] == (
            resumo.NAO_AVALIAVEL, "processo_falhou"
        )

    def test_rodada_ainda_curta_RECUSA_sem_chamar_de_reprovada(self):
        """Faltar tempo não é reprovar: manda esperar, não manda desistir."""
        relato = _relato()
        relato["vigilia"]["da_rodada"]["parede_s"] = 3600.0
        assert resumo._julgar([relato])[:2] == (
            resumo.NAO_AVALIAVEL, "curta_demais"
        )

    def test_a_invalidacao_vem_ANTES_da_conta(self):
        """Rodada confundida com líquido positivo não é PASSA com ressalva."""
        relato = _relato(
            regras={"recolhe_quando_o_livro_anda": True, "pausa_apos_fill_toxico_s": 30.0}
        )
        relato["maker"]["caixa"]["liquido_pro_rata_usdc"] = 999.0
        assert resumo._julgar([relato])[0] == resumo.NAO_AVALIAVEL


class TestZeroEstaLigado:
    """O erro que a verdade booleana produziria, e que custaria uma rodada."""

    def test_ancora_de_ZERO_ticks_conta_como_ligada(self):
        regras = {
            "recolhe_quando_o_livro_anda": False,
            "ticks_abaixo_do_microprice": 0,
            "pausa_apos_fill_toxico_s": None,
        }
        assert resumo._ligada(regras, "ticks_abaixo_do_microprice") is True

    def test_pausa_de_ZERO_segundos_conta_como_ligada(self):
        regras = {
            "recolhe_quando_o_livro_anda": False,
            "ticks_abaixo_do_microprice": None,
            "pausa_apos_fill_toxico_s": 0.0,
        }
        assert resumo._ligada(regras, "pausa_apos_fill_toxico_s") is True

    def test_uma_rodada_com_ancora_em_zero_mais_o_recolher_e_CONFUNDIDA(self):
        """Com verdade booleana esta rodada passaria por rodada do recolher.

        Ela mede as duas, não distingue nenhuma, e o quadro registraria o
        resultado no item errado.
        """
        relato = _relato(
            regras={
                "recolhe_quando_o_livro_anda": True,
                "ticks_abaixo_do_microprice": 0,
            }
        )
        assert resumo._julgar([relato])[:2] == (
            resumo.NAO_AVALIAVEL, "rodada_confundida"
        )

    def test_nenhuma_ligada_e_a_rodada_BASE(self):
        regras, recusa = resumo.regras_da_rodada([_relato()])
        assert recusa is None
        assert [n for n in resumo.REGRAS_EXPERIMENTAIS if resumo._ligada(regras, n)] == []


class TestALeituraDoArquivo:
    def _escrever(self, tmp_path: Path, linhas: list[str]) -> str:
        pasta = tmp_path / "relatorios"
        pasta.mkdir(exist_ok=True)
        alvo = pasta / "RELATOS.jsonl"
        alvo.write_text("\n".join(linhas) + "\n", encoding="utf-8")
        return "relatorios/RELATOS.jsonl"

    def test_descarta_linha_que_nao_e_relato_sem_derrubar_a_leitura(
        self, tmp_path, monkeypatch
    ):
        """`journalctl` intercala linhas do systemd com as do processo.

        Abortar por causa delas obrigaria a filtrar antes — e quem filtra
        errado perde o relato, não a linha do systemd.
        """
        monkeypatch.chdir(tmp_path)
        caminho = self._escrever(tmp_path, [
            "-- Journal begins at Mon 2026-09-01 --",
            json.dumps({"msg": "laco maker nao subiu: sem caminho de diario"}),
            json.dumps(_relato()),
            "",
        ])
        lidos = resumo.ler_relatos(caminho)
        assert len(lidos) == 1
        assert lidos[0]["maker"]["caixa"]["liquido_pro_rata_usdc"] == 150.0

    def test_caminho_fora_da_raiz_RECUSA(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ValueError, match="nome de entrada inválido"):
            resumo.ler_relatos("../fora.jsonl")

    def test_relatos_json_continua_recusado_por_extensao(self, tmp_path, monkeypatch):
        """O leitor pede `.jsonl`; `.json` é outro arquivo, e o erro diz isso."""
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ValueError, match=r"terminando em \.jsonl"):
            resumo.ler_relatos("relatorios/RELATOS.json")


class TestODiario:
    def _diario(self, tmp_path: Path, registros: list[dict]) -> str:
        pasta = tmp_path / "data" / "diarios"
        pasta.mkdir(parents=True, exist_ok=True)
        alvo = pasta / "shadow-maker-4-2.jsonl"
        alvo.write_text(
            "\n".join(json.dumps(r) for r in registros) + "\n", encoding="utf-8"
        )
        return "data/diarios/shadow-maker-4-2.jsonl"

    def test_conta_repousos_do_campo_que_o_produtor_ja_calculou(
        self, tmp_path, monkeypatch
    ):
        """`segundos_repousada` vem do `ClienteSombraDeOrdens`, não daqui.

        Recalcular por diferença de carimbos seria uma segunda conta do mesmo
        número, e a divergência entre as duas apareceria como comportamento.
        """
        monkeypatch.chdir(tmp_path)
        caminho = self._diario(tmp_path, [
            {"evento": "cotacao_colocada", "order_id": "sombra-1-abc"},
            {"evento": "cotacao_colocada", "order_id": "sombra-2-def"},
            {"evento": "cotacao_cancelada", "order_id": "sombra-1-abc",
             "segundos_repousada": 30.0},
            {"evento": "cotacao_cancelada", "order_id": "sombra-2-def",
             "segundos_repousada": 900.0},
        ])
        saida = resumo.conferir_diario(caminho)
        assert saida["colocadas"] == 2
        assert saida["encerradas"] == 2
        assert saida["repouso_s"]["max"] == 900.0
        assert saida["ids_sem_prefixo_de_sombra"] == []

    def test_id_SEM_o_prefixo_de_sombra_e_denunciado(self, tmp_path, monkeypatch):
        """RUNBOOK §10.1: significaria ORDEM REAL, e a instrução é parar.

        Hoje essa linha é conferida a olho no `journalctl`. Conferi-la a cada
        leitura custa uma comparação de string.
        """
        monkeypatch.chdir(tmp_path)
        caminho = self._diario(tmp_path, [
            {"evento": "cotacao_colocada", "order_id": "0xdeadbeef"},
        ])
        assert resumo.conferir_diario(caminho)["ids_sem_prefixo_de_sombra"] == [
            "0xdeadbeef"
        ]

    def test_o_diario_do_4_2_em_data_diarios_e_ACEITO(self, tmp_path, monkeypatch):
        """A pasta que a unit usa. Era recusada pelo `confere_diario_maker`."""
        monkeypatch.chdir(tmp_path)
        caminho = self._diario(tmp_path, [{"evento": "cotacao_colocada"}])
        assert resumo.conferir_diario(caminho)["colocadas"] == 1

    def test_pasta_de_fora_RECUSA(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "outra").mkdir()
        (tmp_path / "outra" / "x.jsonl").write_text("{}\n", encoding="utf-8")
        with pytest.raises(ValueError, match="nome de diário inválido"):
            resumo.conferir_diario("outra/x.jsonl")


class TestASaida:
    def test_o_main_roda_de_ponta_a_ponta_e_diz_o_veredito(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "relatorios").mkdir()
        (tmp_path / "relatorios" / "R.jsonl").write_text(
            json.dumps(_relato()) + "\n", encoding="utf-8"
        )
        assert resumo.main(["--relatos", "relatorios/R.jsonl"]) == 0
        saida = capsys.readouterr().out
        assert resumo.PASSA in saida
        assert "rodada BASE" in saida

    def test_sem_markout_medido_o_resumo_DIZ_que_nao_mediu_custo(
        self, tmp_path, monkeypatch, capsys
    ):
        """`medi custo zero` e `não medi custo` são coisas opostas.

        É a mesma distinção do `sem_recortes` no 1.6: sem ela, uma rodada que
        não observou execução nenhuma sai com o líquido igual aos rewards e
        parece a melhor rodada de todas.
        """
        monkeypatch.chdir(tmp_path)
        relato = _relato()
        relato["maker"]["caixa"]["markout"]["medidas"] = 0
        relato["maker"]["caixa"]["markout"]["resultado_usdc"] = 0.0
        (tmp_path / "relatorios").mkdir()
        (tmp_path / "relatorios" / "R.jsonl").write_text(
            json.dumps(relato) + "\n", encoding="utf-8"
        )
        resumo.main(["--relatos", "relatorios/R.jsonl"])
        saida = capsys.readouterr().out
        assert "não medi custo" in saida

    def test_motivo_unico_sem_pool_de_reward_denuncia_o_opt_in(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.chdir(tmp_path)
        relato = _relato()
        relato["maker"]["motivos"] = {"sem_pool_de_reward": 20160}
        (tmp_path / "relatorios").mkdir()
        (tmp_path / "relatorios" / "R.jsonl").write_text(
            json.dumps(relato) + "\n", encoding="utf-8"
        )
        resumo.main(["--relatos", "relatorios/R.jsonl"])
        assert "o opt-in não pegou" in capsys.readouterr().out

    def test_o_resumo_nao_diz_que_a_rota_pode_ir_a_LIVE(
        self, tmp_path, monkeypatch, capsys
    ):
        """Um PASSA aqui fecha UM item, e o 3.4 continua fechado.

        O leitor existe para alguém decidir a partir dele, e a decisão que
        ele NÃO autoriza tem de estar impressa na mesma tela.
        """
        monkeypatch.chdir(tmp_path)
        (tmp_path / "relatorios").mkdir()
        (tmp_path / "relatorios" / "R.jsonl").write_text(
            json.dumps(_relato()) + "\n", encoding="utf-8"
        )
        resumo.main(["--relatos", "relatorios/R.jsonl"])
        assert "trava tripla do 3.4 continua fechada" in capsys.readouterr().out

    def test_todo_motivo_de_recusa_tem_texto(self):
        """Recusa anônima não vira métrica — e `MOTIVOS[motivo]` explodiria."""
        for nome, texto in resumo.MOTIVOS.items():
            assert texto and not texto.endswith("."), nome

    def test_o_tempo_repousando_sai_em_COTACAO_horas_e_nao_em_dias(
        self, tmp_path, monkeypatch, capsys
    ):
        """`segundos_repousando` soma TODAS as cotações, não é tempo de parede.

        Com 60 cotações no livro ele passa de longe os 14 dias da rodada.
        Impresso em dias ao lado da parede, seria lido como fração da rodada
        — e uma rodada com 11,7 "dias" repousando de 14 pareceria ter ficado
        3 dias fora do livro. É a unidade que o quadro usa nas r7/r8.
        """
        monkeypatch.chdir(tmp_path)
        relato = _relato()
        relato["maker"]["caixa"]["segundos_repousando"] = 3600.0 * 5000
        (tmp_path / "relatorios").mkdir()
        (tmp_path / "relatorios" / "R.jsonl").write_text(
            json.dumps(relato) + "\n", encoding="utf-8"
        )
        resumo.main(["--relatos", "relatorios/R.jsonl"])
        saida = capsys.readouterr().out
        assert "5000.0 cotação-horas" in saida
        assert "208.33 d" not in saida
