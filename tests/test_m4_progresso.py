"""M2.15 — o backtest precisa dizer onde está.

Ele passou a vida inteira sem imprimir nada até terminar. Numa gravação de
24 h isso são mais de três horas de silêncio absoluto, e a única leitura
possível de fora é "travou". Foi exatamente o que aconteceu em 2026-08-26.
"""

from __future__ import annotations

import json

import pytest

from pulsearb.backtest.__main__ import PASSO_DO_PROGRESSO, Progresso, _rss_gib


class TestVaiParaStderr:
    """O relatório sai por stdout. Progresso ali corromperia o JSON.

    Quem redireciona `> relatorio.json` receberia um arquivo que não parseia.
    """

    def test_nada_no_stdout(self, capsys):
        progresso = Progresso()
        progresso.passada("passada 1", arquivos=3)
        progresso.terminou("passada 1", 1000)

        capturado = capsys.readouterr()
        assert capturado.out == ""
        assert "passada 1" in capturado.err

    def test_um_json_no_stdout_sobrevive_ao_progresso(self, capsys):
        progresso = Progresso()
        progresso.passada("passada 2", arquivos=1)
        print(json.dumps({"relatorio": "inteiro"}))
        progresso.terminou("passada 2", 500)

        capturado = capsys.readouterr()
        assert json.loads(capturado.out) == {"relatorio": "inteiro"}


class TestIntervalo:
    def test_so_fala_a_cada_passo(self, capsys):
        progresso = Progresso()
        progresso.passada("p", arquivos=1)
        capsys.readouterr()

        progresso.talvez("p", PASSO_DO_PROGRESSO - 1)
        assert capsys.readouterr().err == ""

        progresso.talvez("p", PASSO_DO_PROGRESSO)
        assert "registros" in capsys.readouterr().err

    def test_o_contador_reinicia_a_cada_passada(self, capsys):
        # Sem isso a passada 2 herdaria o contador da 1 e só falaria de novo
        # meio milhão de registros depois do que devia.
        progresso = Progresso()
        progresso.passada("p1", arquivos=1)
        progresso.talvez("p1", PASSO_DO_PROGRESSO)
        capsys.readouterr()

        progresso.passada("p2", arquivos=1)
        progresso.talvez("p2", PASSO_DO_PROGRESSO)
        assert "p2" in capsys.readouterr().err

    def test_desligado_nao_fala(self, capsys):
        progresso = Progresso(ativo=False)
        progresso.passada("p", arquivos=1)
        progresso.talvez("p", PASSO_DO_PROGRESSO * 10)
        progresso.terminou("p", 1)

        assert capsys.readouterr().err == ""


class TestMemoria:
    """`ru_maxrss` é BYTES no macOS e KILOBYTES no Linux.

    A conta errada dá 1024× de diferença. Como a máquina de análise é um Mac e
    os testes rodam em Linux, o erro passaria despercebido nos dois lugares
    por motivos opostos — no Mac pareceria memória ridícula, no Linux
    impossível.
    """

    @pytest.mark.parametrize(
        ("plataforma", "bruto", "esperado_gib"),
        [
            ("darwin", 4 * 1024**3, 4.0),   # macOS entrega bytes
            ("linux", 4 * 1024**2, 4.0),    # Linux entrega kilobytes
        ],
    )
    def test_a_unidade_muda_com_a_plataforma(
        self, monkeypatch, plataforma, bruto, esperado_gib
    ):
        import resource as modulo_resource

        class _Uso:
            ru_maxrss = bruto

        monkeypatch.setattr("sys.platform", plataforma)
        monkeypatch.setattr(modulo_resource, "getrusage", lambda _: _Uso())

        assert _rss_gib() == pytest.approx(esperado_gib)

    def test_o_progresso_reporta_memoria(self, capsys):
        # O modo real de falhar numa máquina de análise não é erro: é swap. E
        # swap não parece travamento, parece lentidão sem fim.
        progresso = Progresso()
        progresso.passada("p", arquivos=1)
        capsys.readouterr()
        progresso.talvez("p", PASSO_DO_PROGRESSO)

        assert "rss" in capsys.readouterr().err
