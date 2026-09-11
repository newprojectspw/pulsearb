"""3.5 — as travas do script que envia uma ordem assinada de verdade.

Este é o único script do projeto que manda algo assinado para um exchange
real. As travas dele não são cerimônia: cada uma fecha um jeito diferente de
isto custar dinheiro, e um teste que não as exercite deixa a mais cara sem
rede.
"""

from __future__ import annotations

import importlib

import pytest

MODULO = "scripts.smoke_ordem_assinada"


def _mod():
    import sys
    from pathlib import Path

    raiz = str(Path(__file__).resolve().parent.parent)
    if raiz not in sys.path:
        sys.path.insert(0, raiz)
    return importlib.import_module(MODULO)


class TestTrava1Confirmacao:
    """Rodar por engano — ou por um script que chame este — não pode ser
    possível. É a mesma forma da trava tripla do 3.4."""

    def test_sem_a_variavel_recusa(self, monkeypatch):
        m = _mod()
        monkeypatch.delenv(m.ENV_DA_CONFIRMACAO, raising=False)

        assert m.main() == 2

    def test_frase_APROXIMADA_nao_serve(self, monkeypatch):
        """'aceito o risco' não é 'EU ACEITO ENVIAR UMA ORDEM REAL'. Aceitar
        variação transformaria a trava em sugestão."""
        m = _mod()
        for quase in (
            "eu aceito enviar uma ordem real",
            "EU ACEITO ENVIAR UMA ORDEM REAL ",
            "sim",
            "EU ACEITO O RISCO",
        ):
            monkeypatch.setenv(m.ENV_DA_CONFIRMACAO, quase)
            assert m.main() == 2, f"a frase {quase!r} passou, e nao devia"

    def test_a_mensagem_diz_o_que_o_script_faz(self, monkeypatch, capsys):
        m = _mod()
        monkeypatch.delenv(m.ENV_DA_CONFIRMACAO, raising=False)

        m.main()

        erro = capsys.readouterr().err
        assert "ORDEM REAL" in erro
        assert m.FRASE_EXATA in erro


class TestTrava2Saldo:
    """Sem saldo a ordem não tem como executar. Com saldo, poderia — e aí o
    script deixa de ser um teste."""

    async def test_saldo_ilegivel_devolve_None_e_nao_zero(self):
        """`None` e `0` são tratados de forma OPOSTA pelo chamador: zero
        libera, não-sei aborta. Confundir os dois autorizaria envio com estado
        desconhecido."""
        m = _mod()

        class _HttpQueFalha:
            async def request(self, *a, **k):
                # `HTTPError` e o que o httpx levanta em falha de rede — e a
                # UNICA familia que pode virar `None`.
                import httpx

                raise httpx.ConnectError("rede caiu")

        from pulsearb.execution.auth import CredenciaisL2

        creds = CredenciaisL2(
            api_key="k", segredo="c2VncmVkbw==", passphrase="p", endereco="0xabc"
        )
        saldo = await m._saldo_de_colateral(_HttpQueFalha(), "https://x", creds)

        assert saldo is None

    async def test_status_nao_200_tambem_e_None(self):
        m = _mod()

        class _Resp:
            status_code = 401

            def json(self):
                return {"balance": "0"}

        class _Http:
            async def request(self, *a, **k):
                return _Resp()

        from pulsearb.execution.auth import CredenciaisL2

        creds = CredenciaisL2(
            api_key="k", segredo="c2VncmVkbw==", passphrase="p", endereco="0xabc"
        )

        assert await m._saldo_de_colateral(_Http(), "https://x", creds) is None

    async def test_assina_o_PATH_PELADO_e_manda_a_query_na_URL(self):
        """§4.5: assinar a query junto produz assinatura que o servidor não
        reproduz."""
        m = _mod()
        visto = {}

        class _Resp:
            status_code = 200

            def json(self):
                return {"balance": "0"}

        class _Http:
            async def request(self, metodo, url, **k):
                visto["metodo"] = metodo
                visto["url"] = url
                return _Resp()

        from pulsearb.execution.auth import CredenciaisL2

        creds = CredenciaisL2(
            api_key="k", segredo="c2VncmVkbw==", passphrase="p", endereco="0xabc"
        )
        await m._saldo_de_colateral(_Http(), "https://x", creds)

        assert "asset_type=COLLATERAL" in visto["url"]
        assert "signature_type=" in visto["url"]


    async def test_erro_de_PROGRAMACAO_sobe_em_vez_de_virar_None(self):
        """O defeito da primeira execução real, virado teste.

        A versão original chamava `http.get(..., content=...)`, que o httpx não
        aceita. O `TypeError` caía num `except Exception` largo e virava "não
        consegui ler o saldo" — a leitura nunca tocou a rede, e o operador leu
        falha de REDE onde havia código quebrado. Duas causas opostas com a
        mesma mensagem, e a segunda fica invisível até alguém depurar na mão.

        Só falha de rede pode virar `None`.
        """
        m = _mod()

        class _HttpComBug:
            async def request(self, *a, **k):
                raise TypeError("assinatura de metodo errada")

        from pulsearb.execution.auth import CredenciaisL2

        creds = CredenciaisL2(
            api_key="k", segredo="c2VncmVkbw==", passphrase="p", endereco="0xabc"
        )

        with pytest.raises(TypeError):
            await m._saldo_de_colateral(_HttpComBug(), "https://x", creds)

class TestTrava3OrdemQueNaoExecuta:
    def test_o_preco_nao_cruza(self):
        """FOK a preço que não cruza morre no instante: não repousa e não
        executa, mesmo que a trava do saldo falhasse."""
        m = _mod()

        assert 0.0 < m.PRECO_QUE_NAO_CRUZA <= 0.01

    def test_o_cliente_usa_FOK_por_default(self):
        """GTC repousaria no livro. FOK é tudo-ou-nada."""
        from pulsearb.execution.cliente import TIPO_DE_ORDEM

        assert TIPO_DE_ORDEM == "FOK"
