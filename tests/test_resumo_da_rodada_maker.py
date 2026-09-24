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
from copy import deepcopy
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
        # A linha de FIM. Sem ela o relato é um corte do fluxo, e o padrão
        # das fixtures aqui é a rodada que terminou — os cortes têm testes
        # próprios em `TestAsQuatroManeirasDePerderQuatorzeDias`.
        "fim_da_rodada": True,
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


class TestOsAchadosDaRevisao:
    """Quatro defeitos do leitor, e o pior deles o quebrava por completo."""

    def test_rodada_que_TERMINOU_e_a_unica_que_conta_as_duas_semanas(self):
        """O defeito que tornava o leitor inútil (revisão do Codex, #131).

        `laco_de_relato` RETORNA quando o prazo vence, então o último relato
        de 60 s sai sempre ANTES do fim: `parede_s` do último relato é sempre
        menor que as duas semanas. E o estado final saía só por `print`, em
        stdout, sem `msg` e com `indent=2` — fora do fluxo de relatos por
        definição, e fora do `grep` documentado.

        Resultado: TODA rodada de 14 dias bem sucedida saía `curta_demais`.
        O leitor jamais diria PASSA.
        """
        sem_fim = _relato()
        del sem_fim["fim_da_rodada"]

        assert resumo._julgar([sem_fim])[:2] == (
            resumo.NAO_AVALIAVEL, "rodada_nao_terminou"
        )
        assert resumo._julgar([_relato()])[:2] == (resumo.PASSA, None)

    def test_o_processo_de_verdade_EMITE_a_linha_de_fim(self, tmp_path):
        """Percorrido no fonte do produtor, não presumido.

        O conserto tem duas metades em dois arquivos, e a metade que falta
        não dá erro: sem esta linha no `shadow.main`, o leitor passa a
        recusar toda rodada com `rodada_nao_terminou` — o defeito trocado de
        lugar, não consertado.
        """
        fonte = (
            Path(__file__).resolve().parents[1]
            / "src" / "pulsearb" / "live" / "shadow.py"
        ).read_text(encoding="utf-8")

        assert f'log.info("shadow", **estado, {resumo.CAMPO_DO_FIM}=True)' in fonte

    def test_zero_medidas_de_markout_RECUSA_mesmo_com_liquido_positivo(self):
        """Rewards puros não são a conta — e o aviso estava só no rodapé.

        Com zero medidas, o líquido é rewards sem custo nenhum descontado, e
        o custo da rota é justamente o markout. Zero medidas é `não observei
        o custo`, não `o custo é zero` — mesma distinção do `sem_recortes` no
        1.6. O veredito aprovava assim mesmo (revisão do Codex, #131).
        """
        relato = _relato()
        relato["maker"]["caixa"]["markout"]["medidas"] = 0
        relato["maker"]["caixa"]["markout"]["resultado_usdc"] = 0.0
        relato["maker"]["caixa"]["liquido_pro_rata_usdc"] = 200.0

        assert resumo._julgar([relato])[:2] == (
            resumo.NAO_AVALIAVEL, "custo_nao_medido"
        )

    def test_id_sem_prefixo_de_sombra_DERRUBA_o_veredito(self):
        """Um ensaio que pode ter mandado ordem REAL não é um SHADOW válido.

        A conferência existia e era só impressa: o veredito saía PASSA com o
        aviso logo acima (revisão do Codex, #131).
        """
        diario = {
            "colocadas": 1, "encerradas": 0,
            "repouso_s": {"p50": None, "p90": None, "max": None},
            "ids_sem_prefixo_de_sombra": ["0xdeadbeef"],
        }

        assert resumo._julgar([_relato()], diario)[:2] == (
            resumo.NAO_AVALIAVEL, "ordem_sem_prefixo_de_sombra"
        )

    def test_o_id_real_recusa_ATE_sem_relato_nenhum(self):
        """Não é recusa sobre a medida — vale sem medida nenhuma."""
        diario = {
            "colocadas": 1, "encerradas": 0,
            "repouso_s": {"p50": None, "p90": None, "max": None},
            "ids_sem_prefixo_de_sombra": ["0xdeadbeef"],
        }

        assert resumo._julgar([], diario)[1] == "ordem_sem_prefixo_de_sombra"

    def test_rodada_que_dormiu_vence_a_que_nao_terminou(self):
        """Das duas recusas simultâneas, a acionável AGORA é a que sai.

        "Não terminou" no meio de uma rodada de 14 dias é a notícia
        esperada; "dormiu" é o que faz esperar não adiantar.
        """
        relato = _relato()
        del relato["fim_da_rodada"]
        relato["vigilia"]["da_rodada"]["ciclo_de_trabalho"] = 0.12

        assert resumo._julgar([relato])[1] == "rodada_dormiu"

    def test_rodada_sem_restart_nao_diz_nada_sobre_restart(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.chdir(tmp_path)
        antes, depois = _relato(), _relato()
        antes["vigilia"]["da_rodada"]["parede_s"] = 200.0
        (tmp_path / "relatorios").mkdir()
        (tmp_path / "relatorios" / "R.jsonl").write_text(
            "\n".join(json.dumps(r) for r in (antes, depois)) + "\n", encoding="utf-8"
        )

        resumo.main(["--relatos", "relatorios/R.jsonl"])

        assert "O PROCESSO VOLTOU" not in capsys.readouterr().out

    def test_todo_motivo_declarado_e_ALCANCAVEL(self):
        """Dois destes motivos estavam escritos em `MOTIVOS` e nunca eram
        devolvidos por `_julgar` — a recusa existia no papel e o veredito
        aprovava assim mesmo. É o mesmo defeito do `limite_pessimista`, de
        novo: o campo existia e não chegava a quem lê.
        """
        fonte = (
            Path(__file__).resolve().parents[1]
            / "scripts" / "resumo_da_rodada_maker.py"
        ).read_text(encoding="utf-8")
        corpo = fonte.split("MOTIVOS = {", 1)[1].split("\n}\n", 1)[1]

        nao_usados = [
            nome for nome in resumo.MOTIVOS
            if f'"{nome}"' not in corpo
        ]

        assert not nao_usados, (
            f"motivos declarados e nunca devolvidos: {nao_usados}"
        )


class TestASomaDosTrechos:
    """`Restart` devolve a rodada; a caixa não devolve a conta.

    A `CaixaDoMaker` não persiste nada e o `run` refaz `inicio_parede`, então
    cada subida conta do zero. Ler só o último relato reportaria o trecho
    desde a última volta como se fosse a rodada inteira — e como os campos da
    caixa são cumulativos DESDE A SUBIDA, o último relato de cada trecho é o
    total daquele trecho e a soma deles é o total da rodada.
    """

    def _dois_trechos(self):
        """Sete dias, queda, mais sete dias — cada um com metade da conta.

        Devolve o ÚLTIMO relato de cada trecho: é ele que carrega o total do
        trecho, porque os campos da caixa são cumulativos desde a subida.
        """
        metade = resumo.PAREDE_EXIGIDA_S / 2
        a, b = _relato(), _relato()
        del a["fim_da_rodada"]
        for relato in (a, b):
            relato["vigilia"]["da_rodada"]["parede_s"] = metade
            relato["maker"]["caixa"]["rewards_pro_rata_usdc"] = 100.0
            relato["maker"]["caixa"]["markout"]["resultado_usdc"] = -25.0
            relato["maker"]["caixa"]["liquido_pro_rata_usdc"] = 75.0
            relato["maker"]["caixa"]["markout"]["medidas"] = 26
            relato["maker"]["motivos"] = {"repousada": 450}
        return a, b

    def _fluxo(self, a, b):
        """O fluxo como o journal o traz: o primeiro relato do trecho 2 vem
        com `parede_s` baixo, e é essa QUEDA que marca a subida nova."""
        volta = deepcopy(b)
        volta["vigilia"]["da_rodada"]["parede_s"] = 60.0
        return [a, volta, b]

    def test_a_queda_de_parede_s_corta_o_trecho(self):
        a, b = self._dois_trechos()

        assert len(resumo.segmentos(self._fluxo(a, b))) == 2
        assert len(resumo.segmentos([a])) == 1

    def test_dentro_de_um_trecho_parede_s_so_cresce_e_nao_corta(self):
        antes, depois = _relato(), _relato()
        antes["vigilia"]["da_rodada"]["parede_s"] = 100.0
        depois["vigilia"]["da_rodada"]["parede_s"] = 200.0

        assert len(resumo.segmentos([antes, depois])) == 1

    def test_os_dois_trechos_SOMAM_e_fecham_as_duas_semanas(self):
        """Sem somar, cada trecho tem 7 dias e a rodada sairia `curta_demais`.

        Este é o custo real de ler só o último relato: 14 dias de medida
        legítima jogados fora porque a rede piscou no meio.
        """
        a, b = self._dois_trechos()

        veredito, motivo, lido = resumo._julgar(self._fluxo(a, b))

        assert (veredito, motivo) == (resumo.PASSA, None)
        assert lido[resumo.CAMPO_DA_PAREDE] == resumo.PAREDE_EXIGIDA_S
        assert lido[resumo.CAMPO_DOS_REWARDS] == 200.0
        assert lido[resumo.CAMPO_DO_LIQUIDO] == 150.0
        assert lido[resumo.CAMPO_DAS_MEDIDAS] == 52

    def test_um_trecho_so_devolve_o_relato_como_veio(self):
        """Sem restart, nada é somado — o caminho comum não muda."""
        relato = _relato()

        assert resumo.relato_da_rodada([relato]) is relato

    def test_o_markout_em_centavos_e_PONDERADO_pelas_shares(self):
        """Somar médias inventaria número; média simples daria o mesmo peso a
        um trecho de 5 min e a um de 13 dias."""
        a, b = self._dois_trechos()
        a["maker"]["caixa"]["markout"]["centavos_por_share"] = -1.0
        a["maker"]["caixa"]["markout"]["shares"] = 1000.0
        b["maker"]["caixa"]["markout"]["centavos_por_share"] = -0.2
        b["maker"]["caixa"]["markout"]["shares"] = 3000.0

        somado = resumo.relato_da_rodada(self._fluxo(a, b))

        assert somado["maker"]["caixa"]["markout"]["centavos_por_share"] == -0.4

    def test_o_ciclo_de_trabalho_e_PONDERADO_pelo_tempo_de_parede(self):
        a, b = self._dois_trechos()
        a["vigilia"]["da_rodada"]["parede_s"] = 3600.0
        a["vigilia"]["da_rodada"]["ciclo_de_trabalho"] = 0.5
        b["vigilia"]["da_rodada"]["parede_s"] = 3600.0 * 3
        b["vigilia"]["da_rodada"]["ciclo_de_trabalho"] = 1.0

        somado = resumo.relato_da_rodada(self._fluxo(a, b))

        assert somado["vigilia"]["da_rodada"]["ciclo_de_trabalho"] == 0.875

    def test_os_motivos_somam_por_chave(self):
        """`motivos` é cumulativo desde a subida: o último trecho sozinho
        contaria só as passadas desde a última volta."""
        a, b = self._dois_trechos()
        a["maker"]["motivos"] = {"repousada": 100, "sem_pool_de_reward": 7}
        b["maker"]["motivos"] = {"repousada": 300, "manter": 5}

        motivos = resumo.relato_da_rodada(self._fluxo(a, b))["maker"]["motivos"]

        assert motivos == {"manter": 5, "repousada": 400, "sem_pool_de_reward": 7}

    def test_o_tempo_FORA_DO_AR_nao_conta_para_as_duas_semanas(self):
        """Somar `parede_s` dos trechos mede tempo OBSERVADO, não calendário.

        É conservador de propósito: a rodada leva mais de 14 dias de
        calendário para fechar 14 dias de medida, e é o número certo — o que
        o processo não viu não vira observação.
        """
        a, b = self._dois_trechos()
        a["vigilia"]["da_rodada"]["parede_s"] = resumo.PAREDE_EXIGIDA_S / 2
        b["vigilia"]["da_rodada"]["parede_s"] = resumo.PAREDE_EXIGIDA_S / 2 - 3600

        veredito, motivo, _ = resumo._julgar(self._fluxo(a, b))

        assert (veredito, motivo) == (resumo.NAO_AVALIAVEL, "curta_demais")

    def test_o_fim_da_rodada_vem_do_ULTIMO_trecho(self):
        """Um trecho antigo que terminou não faz a rodada ter terminado."""
        a, b = self._dois_trechos()
        a["fim_da_rodada"] = True
        del b["fim_da_rodada"]

        assert resumo._julgar(self._fluxo(a, b))[1] == "rodada_nao_terminou"

    def test_o_resumo_DIZ_quantas_vezes_o_processo_voltou(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.chdir(tmp_path)
        a, b = self._dois_trechos()
        (tmp_path / "relatorios").mkdir()
        (tmp_path / "relatorios" / "R.jsonl").write_text(
            "\n".join(json.dumps(r) for r in self._fluxo(a, b)) + "\n",
            encoding="utf-8",
        )

        resumo.main(["--relatos", "relatorios/R.jsonl"])
        saida = capsys.readouterr().out

        assert "O PROCESSO VOLTOU 1x" in saida
        assert "SOMA de 2 trechos" in saida

    def test_trecho_SEM_o_campo_nao_herda_o_valor_do_ultimo(self):
        """13 dias sem maker + 1 dia medido não são 14 dias de medida.

        `parede_s` somava os dois trechos e o líquido ficava o do ÚLTIMO —
        14 dias no relógio, um dia na conta, veredito PASSA (revisão do
        Codex, #131). Parcela que falta zera o campo, e `None` cai no
        `campo_ausente`, que é o que a situação é: a rodada não tem esse
        número.
        """
        treze_dias = resumo.PAREDE_EXIGIDA_S * 13 / 14
        sem_conta, medido = _relato(), _relato()
        del sem_conta["fim_da_rodada"]
        sem_conta["vigilia"]["da_rodada"]["parede_s"] = treze_dias
        del sem_conta["maker"]["caixa"]["liquido_pro_rata_usdc"]
        medido["vigilia"]["da_rodada"]["parede_s"] = resumo.PAREDE_EXIGIDA_S / 14
        volta = deepcopy(medido)
        volta["vigilia"]["da_rodada"]["parede_s"] = 60.0

        veredito, motivo, _ = resumo._julgar([sem_conta, volta, medido])

        assert (veredito, motivo) == (resumo.NAO_AVALIAVEL, "campo_ausente")

    def test_com_todas_as_parcelas_o_campo_soma_normalmente(self):
        """A recusa acima não pode ter engolido o caminho comum."""
        a, b = self._dois_trechos()

        somado = resumo.relato_da_rodada(self._fluxo(a, b))

        assert somado["maker"]["caixa"]["liquido_pro_rata_usdc"] == 150.0


class TestAUnitNaoReiniciaDepoisDoSucesso:
    """`Restart=always` fazia a rodada recomeçar sozinha, para sempre.

    Achado da revisão do Codex (#131): as duas semanas terminavam, o processo
    saía limpo, e o systemd o reiniciava 10 s depois — uma rodada NOVA
    começava sozinha, anexando ao mesmo diário. O `main` já devolve 0 no
    sucesso e 1 quando a rodada não produziu saída, então `on-failure` diz
    exatamente o que se quer.
    """

    def _units(self):
        raiz = Path(__file__).resolve().parents[1] / "deploy"
        return [
            raiz / "pulsearb-shadow-maker.service",
            raiz / "pulsearb-shadow-maker@.service",
        ]

    def test_as_duas_units_sao_on_failure(self):
        for unit in self._units():
            diretivas = [
                linha.strip()
                for linha in unit.read_text(encoding="utf-8").splitlines()
                if linha.strip().startswith("Restart=")
            ]

            assert diretivas == ["Restart=on-failure"], f"{unit.name}: {diretivas}"

    def test_o_shadow_sai_com_ZERO_no_sucesso(self):
        """`on-failure` só funciona se o código de saída disser a verdade.

        Percorrido no fonte: um `return 0` incondicional faria uma rodada
        morta ficar morta, e um `return 1` sempre faria a rodada reiniciar
        para sempre — os dois desfazem este conserto sem tocar na unit.
        """
        fonte = (
            Path(__file__).resolve().parents[1]
            / "src" / "pulsearb" / "live" / "shadow.py"
        ).read_text(encoding="utf-8")

        assert "return 1 if processo.falhou else 0" in fonte


class TestOQueOFimDaRodadaNaoMEDIU:
    """O `run` devolve o estado em memória, e ele não está fechado.

    Duas coisas ficam de fora, e as duas favorecem a rota: markouts cujo
    horizonte de 5 s não passou, e os prints da última cadência de 15 s, que
    o laço não chegou a olhar. Contra 14 dias é pouco, mas uma perna de 1.000
    shares não é ruído — e um `PASSA` com custo omitido é o defeito que este
    arquivo inteiro existe para não produzir (revisão do Codex, #131).

    O conserto de verdade é o motor fechar a conta antes de sair. Enquanto
    não existe, o número sai dito.
    """

    def _com_pendentes(self, quantos):
        relato = _relato()
        relato["maker"]["caixa"]["execucoes_possiveis"][
            "pendentes_de_markout"
        ] = quantos
        return relato

    def test_o_resumo_DIZ_quantas_execucoes_ficaram_sem_markout(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "relatorios").mkdir()
        (tmp_path / "relatorios" / "R.jsonl").write_text(
            json.dumps(self._com_pendentes(2)) + "\n", encoding="utf-8"
        )

        resumo.main(["--relatos", "relatorios/R.jsonl"])
        saida = capsys.readouterr().out

        assert "2 EXECUÇÃO(ÕES) SEM MARKOUT FECHADO" in saida
        assert "LIMITE SUPERIOR" in saida

    def test_sem_pendentes_o_resumo_nao_inventa_ressalva(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "relatorios").mkdir()
        (tmp_path / "relatorios" / "R.jsonl").write_text(
            json.dumps(self._com_pendentes(0)) + "\n", encoding="utf-8"
        )

        resumo.main(["--relatos", "relatorios/R.jsonl"])

        assert "SEM MARKOUT FECHADO" not in capsys.readouterr().out

    def test_pendentes_SOMA_entre_trechos(self):
        """Eu tinha argumentado o contrário, e estava errado.

        Dentro de um trecho o número é instantâneo — mas o que se lê aqui é o
        ÚLTIMO relato de CADA trecho, e para um trecho que MORREU esses são
        fills cujo markout nunca vai fechar: a `CaixaDoMaker` some com o
        processo. Não há dupla contagem (cada trecho tem caixa própria), e é
        a única forma de o fill perdido num trecho antigo aparecer na
        ressalva quando o último trecho termina com zero (revisão do Codex,
        #131).
        """
        a, b = self._com_pendentes(3), self._com_pendentes(2)
        del a["fim_da_rodada"]
        a["vigilia"]["da_rodada"]["parede_s"] = 700_000.0
        volta = deepcopy(b)
        volta["vigilia"]["da_rodada"]["parede_s"] = 60.0

        somado = resumo.relato_da_rodada([a, volta, b])

        assert somado["maker"]["caixa"]["execucoes_possiveis"][
            "pendentes_de_markout"
        ] == 5

    def test_o_fill_perdido_num_trecho_ANTIGO_ainda_sai_na_ressalva(
        self, tmp_path, monkeypatch, capsys
    ):
        """O caso que o `deepcopy` do último trecho escondia por completo."""
        monkeypatch.chdir(tmp_path)
        antes, ultimo = self._com_pendentes(4), self._com_pendentes(0)
        del antes["fim_da_rodada"]
        antes["vigilia"]["da_rodada"]["parede_s"] = 700_000.0
        volta = deepcopy(ultimo)
        volta["vigilia"]["da_rodada"]["parede_s"] = 60.0
        (tmp_path / "relatorios").mkdir()
        (tmp_path / "relatorios" / "R.jsonl").write_text(
            "\n".join(json.dumps(r) for r in (antes, volta, ultimo)) + "\n",
            encoding="utf-8",
        )

        resumo.main(["--relatos", "relatorios/R.jsonl"])

        assert "4 EXECUÇÃO(ÕES) SEM MARKOUT FECHADO" in capsys.readouterr().out


class TestTrechoSemRegras:
    """Trecho sem `maker.regras` RECUSA, em vez de ser filtrado fora.

    Filtrar era um buraco (revisão do Codex, #131): um trecho de versão
    antiga, ou um em que o maker não subiu, sumia da conferência de regras —
    e a soma dos trechos levava os rewards e o markout dele assim mesmo, com
    o veredito validando só a configuração do trecho que TINHA a informação.
    Medida de regra desconhecida entrava num PASSA.
    """

    def test_um_trecho_sem_regras_derruba_a_conferencia(self):
        antes, depois = _relato(), _relato(regras={"recolhe_quando_o_livro_anda": True})
        del antes["maker"]["regras"]

        assert resumo.regras_da_rodada([antes, depois])[1] == "campo_ausente"

    def test_e_o_veredito_RECUSA_em_vez_de_somar_o_trecho_desconhecido(self):
        antes = _relato()
        antes["vigilia"]["da_rodada"]["parede_s"] = 700_000.0
        del antes["fim_da_rodada"]
        del antes["maker"]["regras"]
        volta = deepcopy(_relato())
        volta["vigilia"]["da_rodada"]["parede_s"] = 60.0

        assert resumo._julgar([antes, volta, _relato()])[:2] == (
            resumo.NAO_AVALIAVEL, "campo_ausente"
        )

    def test_todos_os_trechos_com_regras_seguem_normalmente(self):
        """A recusa não pode ter engolido o caminho comum."""
        assert resumo.regras_da_rodada([_relato(), _relato()])[1] is None


class TestKnobNovoNaoInvalidaRodadaAntiga:
    """Um knob acrescentado ao `maker.regras` não pode matar uma rodada em
    curso por um restart pós-deploy.

    O trecho gravado pela versão antiga não traz a chave nova; o novo traz com
    `None` (regra desligada). Comparar os dicts crus daria
    `regras_mudaram_no_meio` — 14 dias invalidados por uma chave ACRESCENTADA,
    não mudada (revisão do Codex, #193).
    """

    def test_chave_nova_desligada_nao_e_mudanca_de_regra(self):
        # `_relato()` tem a forma ANTIGA (sem `fracao_maxima_do_pool`); o novo
        # trecho traz a chave em None (desligada).
        antigo = _relato()
        assert "fracao_maxima_do_pool" not in antigo["maker"]["regras"]
        novo = _relato(regras={"fracao_maxima_do_pool": None})

        assert resumo.regras_da_rodada([antigo, novo])[1] is None

    def test_chave_nova_LIGADA_no_meio_ainda_e_mudanca_de_regra(self):
        """A normalização não pode cegar o caso real: se o teto foi de fato
        LIGADO no meio, a regra mudou e a rodada tem de recusar."""
        antigo = _relato()
        ligou_teto = _relato(regras={"fracao_maxima_do_pool": 0.10})

        assert resumo.regras_da_rodada([antigo, ligou_teto])[1] == (
            "regras_mudaram_no_meio"
        )


class TestODiarioPedidoENaoLido:
    """Pedir o diário e não conseguir lê-lo NÃO é o mesmo que não pedir.

    Seguir sem ele pularia em silêncio a conferência do prefixo `sombra-` —
    o ensaio sairia declarado válido sem que ninguém tivesse olhado o
    artefato de segurança dele (revisão do Codex, #131).
    """

    def _relatos_em(self, tmp_path):
        (tmp_path / "relatorios").mkdir(exist_ok=True)
        (tmp_path / "relatorios" / "R.jsonl").write_text(
            json.dumps(_relato()) + "\n", encoding="utf-8"
        )

    def test_caminho_de_diario_errado_RECUSA_em_vez_de_passar(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.chdir(tmp_path)
        self._relatos_em(tmp_path)

        resumo.main([
            "--relatos", "relatorios/R.jsonl",
            "--diario", "data/diarios/nao-existe.jsonl",
        ])
        saida = capsys.readouterr().out

        assert resumo.NAO_AVALIAVEL in saida
        assert "diario_ilegivel" in saida
        assert resumo.PASSA not in saida.split("4.2 —")[-1]

    def test_diario_FORA_da_pasta_permitida_tambem_RECUSA(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.chdir(tmp_path)
        self._relatos_em(tmp_path)

        resumo.main([
            "--relatos", "relatorios/R.jsonl", "--diario", "outra/x.jsonl",
        ])

        assert "diario_ilegivel" in capsys.readouterr().out

    def test_sem_pedir_diario_o_veredito_segue_normal(
        self, tmp_path, monkeypatch, capsys
    ):
        """A recusa é sobre o diário PEDIDO — não pedir continua válido."""
        monkeypatch.chdir(tmp_path)
        self._relatos_em(tmp_path)

        resumo.main(["--relatos", "relatorios/R.jsonl"])

        assert resumo.PASSA in capsys.readouterr().out
