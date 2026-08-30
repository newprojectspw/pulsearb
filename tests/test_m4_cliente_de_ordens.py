"""3.5 — o cliente de ordens: FOK, idempotência, rejeição e timeout.

O teste que dá nome ao arquivo é o do timeout. Todo o resto existe em volta
dele: um timeout mal classificado vira posição dupla, e posição dupla é o
único erro deste caminho que custa dinheiro sem aparecer em lugar nenhum até
a resolução.
"""

from __future__ import annotations

import asyncio

import pytest

from pulsearb.execution.auth import CredenciaisL2, corpo_canonico
from pulsearb.execution.cliente import (
    ENVIOS_LEMBRADOS,
    MOTIVOS_DE_RECUSA,
    TIMEOUT_DO_ENVIO_S,
    TIPO_DE_ORDEM,
    ClienteDeOrdens,
    ErroDeTransporte,
    EstadoDoEnvio,
    conferir_ordem,
    fazer_transporte,
    id_do_cliente,
)
from pulsearb.risk import OrdemPretendida

CREDENCIAIS = CredenciaisL2(
    api_key="chave", segredo="c2VncmVkbw==", passphrase="frase", endereco="0xabc"
)


def _ordem(slug="btc-updown-5m-1", shares=5.0, preco=0.50, token="tok-up", up=True):
    return OrdemPretendida(
        slug=slug, token_id=token, lado_up=up, shares=shares, preco_limite=preco
    )


class _Construtor:
    """Dublê do assinador da ordem. A chave privada não passa pelo cliente."""

    def __init__(self):
        self.pedidos: list[str] = []

    def corpo_da_ordem(self, ordem, *, id_do_cliente):
        self.pedidos.append(id_do_cliente)
        return {"tokenId": ordem.token_id, "clientId": id_do_cliente}


class _Transporte:
    """Dublê da rede. `respostas` é uma fila; exceção na fila é levantada."""

    def __init__(self, *respostas):
        self.respostas = list(respostas)
        self.chamadas: list[tuple[str, dict, bytes]] = []

    async def __call__(self, caminho, cabecalhos, corpo):
        self.chamadas.append((caminho, cabecalhos, corpo))
        resposta = self.respostas.pop(0) if self.respostas else (200, {"success": True})
        if isinstance(resposta, Exception):
            raise resposta
        return resposta


def _cliente(*respostas, **ajustes):
    return ClienteDeOrdens(
        CREDENCIAIS, _Construtor(), _Transporte(*respostas), **ajustes
    )


class TestTimeoutNaoERecusa:
    """A distinção mais cara do módulo."""

    async def test_transporte_que_falha_vira_INCERTA_e_nao_recusada(self):
        """Se isto virasse `RECUSADA`, quem chama reenviaria — e a ordem
        original pode estar preenchida do outro lado."""
        cliente = _cliente(ErroDeTransporte("timeout depois de 5 s"))

        resultado = await cliente.enviar(_ordem(), janela="j1")

        assert resultado.estado is EstadoDoEnvio.INCERTA
        assert resultado.precisa_reconciliar is True
        assert resultado.order_id is None

    async def test_incerta_carrega_o_id_para_a_reconciliacao_encontrar(self):
        """`order_id` não existe (a resposta não chegou), mas o NOSSO id sim —
        e é por ele que se procura a ordem que talvez tenha entrado."""
        cliente = _cliente(ErroDeTransporte("timeout"))

        resultado = await cliente.enviar(_ordem(), janela="j1")

        assert resultado.id_do_cliente == id_do_cliente(_ordem(), janela="j1")

    async def test_5xx_tambem_e_INCERTA(self):
        """Um 502 pode chegar depois de a ordem passar para o casamento.

        Só a faixa 4xx prova que ela não entrou: ali o servidor examinou o
        pedido e disse não.
        """
        cliente = _cliente((502, None))

        resultado = await cliente.enviar(_ordem(), janela="j1")

        assert resultado.estado is EstadoDoEnvio.INCERTA

    async def test_4xx_e_recusa_de_verdade(self):
        cliente = _cliente((400, {"error": "tick invalido"}))

        resultado = await cliente.enviar(_ordem(), janela="j1")

        assert resultado.estado is EstadoDoEnvio.RECUSADA
        assert resultado.motivo == MOTIVOS_DE_RECUSA.SERVIDOR_RECUSOU
        assert resultado.precisa_reconciliar is False


class TestIdempotencia:
    async def test_a_mesma_ordem_na_mesma_janela_tem_o_mesmo_id(self):
        """É isso que torna a reconciliação possível: procura-se por um id
        conhecido, não se adivinha pelo horário."""
        assert id_do_cliente(_ordem(), janela="j1") == id_do_cliente(
            _ordem(), janela="j1"
        )

    async def test_a_mesma_ordem_em_OUTRA_janela_tem_id_diferente(self):
        """Sem a janela na conta, a segunda ordem — real e distinta — seria
        recusada como duplicata."""
        assert id_do_cliente(_ordem(), janela="j1") != id_do_cliente(
            _ordem(), janela="j2"
        )

    @pytest.mark.parametrize(
        "mudanca",
        [
            {"shares": 6.0},
            {"preco": 0.51},
            {"token": "tok-down"},
            {"up": False},
            {"slug": "btc-updown-5m-2"},
        ],
    )
    async def test_qualquer_campo_que_muda_a_ordem_muda_o_id(self, mudanca):
        assert id_do_cliente(_ordem(**mudanca), janela="j1") != id_do_cliente(
            _ordem(), janela="j1"
        )

    async def test_reenviar_a_mesma_ordem_e_recusado_sem_tocar_na_rede(self):
        cliente = _cliente((200, {"success": True, "orderID": "o1"}))

        primeira = await cliente.enviar(_ordem(), janela="j1")
        segunda = await cliente.enviar(_ordem(), janela="j1")

        assert primeira.estado is EstadoDoEnvio.ACEITA
        assert segunda.estado is EstadoDoEnvio.RECUSADA
        assert segunda.motivo == MOTIVOS_DE_RECUSA.JA_ENVIADA
        assert len(cliente.transporte.chamadas) == 1

    async def test_reenviar_depois_de_INCERTA_e_recusado_SOBRETUDO(self):
        """O caso que mais importa dos dois.

        Depois de um timeout a ordem pode estar no livro. Um cliente que
        deixasse a segunda tentativa passar produziria a posição dupla que o
        módulo inteiro existe para impedir — e o `estado_anterior` no detalhe
        diz a quem chamou por que a recusa aconteceu.
        """
        cliente = _cliente(ErroDeTransporte("timeout"))

        incerta = await cliente.enviar(_ordem(), janela="j1")
        segunda = await cliente.enviar(_ordem(), janela="j1")

        assert incerta.estado is EstadoDoEnvio.INCERTA
        assert segunda.motivo == MOTIVOS_DE_RECUSA.JA_ENVIADA
        assert segunda.detalhe["estado_anterior"] == str(EstadoDoEnvio.INCERTA)
        assert len(cliente.transporte.chamadas) == 1

    async def test_ordem_mal_formada_NAO_trava_o_id(self):
        """Nada saiu para a rede, então travar o id impediria a mesma ordem de
        ser enviada depois de corrigida a configuração."""
        cliente = _cliente((200, {"success": True}))

        recusada = await cliente.enviar(_ordem(shares=1.0), janela="j1")
        assert recusada.motivo == MOTIVOS_DE_RECUSA.ORDEM_MAL_FORMADA

        depois = await cliente.enviar(_ordem(shares=5.0), janela="j1")
        assert depois.estado is EstadoDoEnvio.ACEITA

    async def test_a_memoria_de_envios_tem_teto(self):
        """Sessão de 24 h não pode crescer memória sem limite."""
        cliente = _cliente(*[(200, {"success": True})] * 5, envios_lembrados=3)

        for i in range(5):
            await cliente.enviar(_ordem(), janela=f"j{i}")

        assert len(cliente._enviados) == 3

    def test_o_teto_default_cobre_uma_sessao_inteira(self):
        """5 min por janela × 512 ≈ 42 h, com folga sobre as 24 h do 3.13."""
        assert ENVIOS_LEMBRADOS * 5 / 60 > 24


class TestAConferenciaLocal:
    @pytest.mark.parametrize(
        ("ajuste", "trecho"),
        [
            ({"shares": 0.0}, "positivo"),
            ({"shares": -1.0}, "positivo"),
            ({"shares": 3.0}, "minimo"),
            ({"preco": 0.0}, "fora de"),
            ({"preco": 1.0}, "fora de"),
            ({"preco": 1.5}, "fora de"),
            ({"token": ""}, "token_id"),
        ],
    )
    def test_o_que_o_servidor_recusaria_e_pego_antes_da_rede(self, ajuste, trecho):
        """Troca um timeout de rede por uma mensagem que diz o que está errado."""
        problema = conferir_ordem(_ordem(**ajuste), minimo_de_shares=5.0)

        assert problema is not None and trecho in problema

    def test_ordem_boa_passa(self):
        assert conferir_ordem(_ordem(), minimo_de_shares=5.0) is None

    async def test_a_conferencia_roda_antes_de_gastar_a_rede(self):
        cliente = _cliente((200, {"success": True}))

        await cliente.enviar(_ordem(shares=1.0), janela="j1")

        assert cliente.transporte.chamadas == []


class TestOEnvio:
    async def test_o_tipo_de_ordem_e_sempre_FOK(self):
        """`[VERIFICADO]` API_NOTES §4.1. Parcial numa janela de 5 min é
        exposição com tamanho diferente do autorizado e sem tempo de corrigir."""
        cliente = _cliente((200, {"success": True}))

        await cliente.enviar(_ordem(), janela="j1")

        _, _, corpo = cliente.transporte.chamadas[0]
        assert b'"orderType":"FOK"' in corpo
        assert TIPO_DE_ORDEM == "FOK"

    async def test_o_corpo_no_fio_e_o_corpo_assinado(self):
        """A regra do `auth.py`, exercitada de ponta a ponta: o que o
        transporte recebe é byte a byte o que entrou no HMAC."""
        cliente = _cliente((200, {"success": True}))

        await cliente.enviar(_ordem(), janela="j1")

        _, cabecalhos, corpo = cliente.transporte.chamadas[0]
        esperado = corpo_canonico(
            {
                "tokenId": "tok-up",
                "clientId": id_do_cliente(_ordem(), janela="j1"),
                "orderType": "FOK",
            }
        )
        assert corpo == esperado
        assert "POLY_SIGNATURE" in cabecalhos

    async def test_o_construtor_recebe_o_id_determinístico(self):
        """O id vai no corpo assinado, senão a idempotência do lado deles não
        existe — só a nossa, que não sobrevive a reinício."""
        cliente = _cliente((200, {"success": True}))

        await cliente.enviar(_ordem(), janela="j1")

        assert cliente.construtor.pedidos == [id_do_cliente(_ordem(), janela="j1")]

    async def test_aceita_traz_o_order_id_do_servidor(self):
        cliente = _cliente((200, {"success": True, "orderID": "0xdeadbeef"}))

        resultado = await cliente.enviar(_ordem(), janela="j1")

        assert resultado.estado is EstadoDoEnvio.ACEITA
        assert resultado.order_id == "0xdeadbeef"

    async def test_200_com_success_false_e_recusa(self):
        """200 não é aceite: o CLOB responde 200 com `success: false` em erro
        de negócio, e ler só o status daria posição imaginária."""
        cliente = _cliente((200, {"success": False, "errorMsg": "sem allowance"}))

        resultado = await cliente.enviar(_ordem(), janela="j1")

        assert resultado.estado is EstadoDoEnvio.RECUSADA
        assert resultado.motivo == MOTIVOS_DE_RECUSA.SERVIDOR_RECUSOU

    @pytest.mark.parametrize("status", [401, 403])
    async def test_auth_recusada_tem_motivo_proprio(self, status):
        """Nome separado porque o conserto é outro: credencial errada não se
        resolve tentando de novo, e um alarme de auth não é um alarme de
        mercado."""
        cliente = _cliente((status, {"error": "unauthorized"}))

        resultado = await cliente.enviar(_ordem(), janela="j1")

        assert resultado.motivo == MOTIVOS_DE_RECUSA.AUTH_RECUSADA

    async def test_o_cliente_nao_reenvia_por_conta_propria(self):
        """Sem `retry` embutido: reenviar é a decisão mais perigosa deste
        caminho e não pode morar numa política default."""
        cliente = _cliente(ErroDeTransporte("timeout"))

        await cliente.enviar(_ordem(), janela="j1")

        assert len(cliente.transporte.chamadas) == 1


class TestAJanelaAntesDoAwait:
    """Achado P1 do Codex no #52. A trava só valia se ninguém corresse."""

    async def test_duas_corrotinas_com_a_MESMA_ordem_enviam_uma_vez_so(self):
        """`_lembrar` só depois da resposta deixava uma janela entre a consulta
        e o registro: as duas viam o dicionário vazio, as duas passavam pelo
        `await`, e a ordem saía DUAS VEZES.

        O dublê segura a primeira chamada até a segunda entrar — sem isso o
        teste passaria por acidente de escalonamento, e não por causa da trava.
        """
        entrou = asyncio.Event()
        soltar = asyncio.Event()
        chamadas = []

        async def transporte(caminho, cabecalhos, corpo):
            chamadas.append(caminho)
            entrou.set()
            await soltar.wait()
            return 200, {"success": True, "orderID": "o1"}

        cliente = ClienteDeOrdens(CREDENCIAIS, _Construtor(), transporte)

        primeira = asyncio.create_task(cliente.enviar(_ordem(), janela="j1"))
        await entrou.wait()
        segunda = await cliente.enviar(_ordem(), janela="j1")
        soltar.set()
        r1 = await primeira

        assert len(chamadas) == 1, "a ordem saiu duas vezes"
        assert r1.estado is EstadoDoEnvio.ACEITA
        assert segunda.motivo == MOTIVOS_DE_RECUSA.JA_ENVIADA

    async def test_a_reserva_e_INCERTA_e_nao_um_estado_inventado(self):
        """Se o processo morrer entre o envio e a resposta, INCERTA é
        exatamente o que aconteceu: a ordem pode estar no livro."""
        entrou = asyncio.Event()
        soltar = asyncio.Event()

        async def transporte(caminho, cabecalhos, corpo):
            entrou.set()
            await soltar.wait()
            return 200, {"success": True}

        cliente = ClienteDeOrdens(CREDENCIAIS, _Construtor(), transporte)
        tarefa = asyncio.create_task(cliente.enviar(_ordem(), janela="j1"))
        await entrou.wait()

        assert cliente.ja_enviada(id_do_cliente(_ordem(), janela="j1")) is (
            EstadoDoEnvio.INCERTA
        )

        soltar.set()
        await tarefa


class TestOTimeoutDeclaradoEAplicado:
    """Achado P2 do Codex no #52: eu declarei `TIMEOUT_DO_ENVIO_S` e nunca o
    liguei — uma constante que parece segurança e não é."""

    async def test_transporte_que_engasga_vira_INCERTA_e_nao_pendura(self):
        """CLOB lento ou proxy de rate-limit enfileirando o POST não levantam
        `ErroDeTransporte`: eles simplesmente não voltam. Sem o timeout,
        `enviar` ficava pendurado para sempre e o `INCERTA` obrigatório nunca
        saía."""

        async def transporte(caminho, cabecalhos, corpo):
            await asyncio.sleep(30)
            return 200, {"success": True}

        cliente = ClienteDeOrdens(
            CREDENCIAIS, _Construtor(), transporte, timeout_s=0.02
        )

        resultado = await asyncio.wait_for(
            cliente.enviar(_ordem(), janela="j1"), timeout=2.0
        )

        assert resultado.estado is EstadoDoEnvio.INCERTA
        assert resultado.precisa_reconciliar is True

    async def test_o_default_e_o_declarado(self):
        cliente = _cliente()

        assert cliente.timeout_s == TIMEOUT_DO_ENVIO_S

    async def test_depois_do_timeout_o_reenvio_continua_barrado(self):
        """O caso que junta os dois achados: engasgou, virou INCERTA, e a
        ordem pode estar no livro."""

        async def transporte(caminho, cabecalhos, corpo):
            await asyncio.sleep(30)

        cliente = ClienteDeOrdens(
            CREDENCIAIS, _Construtor(), transporte, timeout_s=0.02
        )

        await cliente.enviar(_ordem(), janela="j1")
        segunda = await cliente.enviar(_ordem(), janela="j1")

        assert segunda.motivo == MOTIVOS_DE_RECUSA.JA_ENVIADA
        assert segunda.detalhe["estado_anterior"] == str(EstadoDoEnvio.INCERTA)


class TestOTransporteReal:
    """3.5 — o adaptador de produção. Sem rede: `httpx.MockTransport`."""

    BASE = "https://clob.polymarket.com"

    def _http(self, manipulador):
        import httpx

        return httpx.AsyncClient(transport=httpx.MockTransport(manipulador))

    async def test_o_corpo_no_fio_e_o_corpo_ASSINADO(self):
        """A linha que a regra do `auth.py` protege.

        `json=` reserializaria o dicionário e os bytes no fio deixariam de ser
        os bytes assinados. O servidor recusaria com 401 e nada apontaria a
        causa. Por isso o transporte usa `content=`.
        """
        import httpx

        vistos = {}

        def manipulador(pedido):
            vistos["corpo"] = pedido.content
            vistos["assinatura"] = pedido.headers.get("poly_signature")
            vistos["tipo"] = pedido.headers.get("content-type")
            return httpx.Response(200, json={"success": True, "orderID": "o1"})

        async with self._http(manipulador) as http:
            transporte = fazer_transporte(http, base_do_clob=self.BASE)
            cliente = ClienteDeOrdens(CREDENCIAIS, _Construtor(), transporte)
            resultado = await cliente.enviar(_ordem(), janela="j1")

        assert resultado.estado is EstadoDoEnvio.ACEITA
        assert resultado.order_id == "o1"
        esperado = corpo_canonico(
            {
                "tokenId": "tok-up",
                "clientId": id_do_cliente(_ordem(), janela="j1"),
                "orderType": "FOK",
            }
        )
        assert vistos["corpo"] == esperado
        assert vistos["assinatura"]
        assert vistos["tipo"] == "application/json"

    async def test_falha_de_rede_vira_INCERTA_e_nao_recusa(self):
        """O teste que mais importa deste adaptador.

        Se ele devolvesse um status inventado num timeout, `_resultado` leria
        "menor que 400" e classificaria como RECUSA — e recusa autoriza
        reenvio. Uma ordem que talvez esteja no livro sairia de novo.
        """
        import httpx

        def manipulador(pedido):
            raise httpx.ConnectTimeout("o servidor nao respondeu")

        async with self._http(manipulador) as http:
            transporte = fazer_transporte(http, base_do_clob=self.BASE)
            cliente = ClienteDeOrdens(CREDENCIAIS, _Construtor(), transporte)
            resultado = await cliente.enviar(_ordem(), janela="j1")

        assert resultado.estado is EstadoDoEnvio.INCERTA
        assert resultado.precisa_reconciliar is True

    @pytest.mark.parametrize(
        "excecao",
        [
            "ConnectError",
            "ReadTimeout",
            "RemoteProtocolError",
            "PoolTimeout",
        ],
    )
    async def test_qualquer_falha_de_transporte_e_INCERTA(self, excecao):
        """Enumerar as classes do httpx deixaria a de fora virar recusa —
        que é o defeito caro. Por isso a captura é larga."""
        import httpx

        def manipulador(pedido):
            raise getattr(httpx, excecao)("falhou")

        async with self._http(manipulador) as http:
            transporte = fazer_transporte(http, base_do_clob=self.BASE)
            cliente = ClienteDeOrdens(CREDENCIAIS, _Construtor(), transporte)
            resultado = await cliente.enviar(_ordem(), janela="j1")

        assert resultado.estado is EstadoDoEnvio.INCERTA

    async def test_corpo_ilegivel_NAO_apaga_o_status(self):
        """Um 502 com HTML de proxy continua sendo incerteza; um 400 continua
        sendo recusa."""
        import httpx

        def manipulador(pedido):
            return httpx.Response(502, text="<html>bad gateway</html>")

        async with self._http(manipulador) as http:
            transporte = fazer_transporte(http, base_do_clob=self.BASE)
            cliente = ClienteDeOrdens(CREDENCIAIS, _Construtor(), transporte)
            resultado = await cliente.enviar(_ordem(), janela="j1")

        assert resultado.estado is EstadoDoEnvio.INCERTA

    async def test_o_POST_so_sai_para_o_CLOB_configurado(self):
        """Defesa em profundidade, como na descoberta."""
        import httpx

        from pulsearb.markets.http import DestinoNaoPermitido

        def manipulador(pedido):
            return httpx.Response(200, json={"success": True})

        async with self._http(manipulador) as http:
            transporte = fazer_transporte(http, base_do_clob=self.BASE)

            with pytest.raises(DestinoNaoPermitido):
                await transporte("../../outro-host/order", {}, b"{}")

    async def test_base_vazia_e_erro_na_construcao(self):
        """O transporte sairia para lugar nenhum, e o erro apareceria só na
        primeira ordem — que é o pior momento possível."""
        with pytest.raises(ValueError):
            fazer_transporte(object(), base_do_clob="")
