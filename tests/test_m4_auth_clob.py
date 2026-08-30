"""3.2 — a autenticação do CLOB.

Cada teste aqui trava um jeito de a assinatura sair errada **em silêncio**.
Nenhuma das falhas cobertas produz exceção no nosso lado: todas produzem uma
recusa do servidor cuja mensagem não diz qual foi o erro. Por isso são testes,
e não confiança na leitura do código.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

import pytest

from pulsearb.execution.auth import (
    ASSINATURA_EOA,
    CHAIN_ID_POLYGON,
    CredenciaisL2,
    ErroDeAuth,
    assinar_l2,
    cabecalhos_l1,
    corpo_canonico,
    typed_data_de_auth,
)

#: Segredo com `-` e `_`: os dois caracteres em que o alfabeto urlsafe difere
#: do padrão. Um segredo sem eles decodifica igual nos dois e não testaria nada.
SEGREDO = base64.urlsafe_b64encode(b"\xfb\xef\xbe segredo do pulsearb").decode()


def _credenciais(**ajustes):
    base = {
        "api_key": "chave-123",
        "segredo": SEGREDO,
        "passphrase": "frase",
        "endereco": "0xabc",
    }
    base.update(ajustes)
    return CredenciaisL2(**base)


class TestOCorpoAssinadoEOCorpoEnviado:
    """A armadilha central do L2, e a razão de a função devolver um par."""

    def test_assinar_devolve_o_corpo_junto_dos_cabecalhos(self):
        """Quem chama não recebe a chance de reserializar.

        `[VERIFICADO]` API_NOTES §3: o corpo assinado precisa ser o body
        pré-serializado EXATO que vai no fio. Se `assinar_l2` devolvesse só
        cabeçalhos, o caminho natural de quem chama seria passar o dict para
        o `httpx` com `json=`, que reserializa — e a assinatura quebraria
        sem nenhum erro do nosso lado.
        """
        cabecalhos, corpo = assinar_l2(
            _credenciais(), metodo="POST", caminho="/order", corpo={"b": 1, "a": 2}
        )

        assert isinstance(corpo, bytes)
        assert corpo == b'{"b":1,"a":2}'
        assert set(cabecalhos) == {
            "POLY_ADDRESS",
            "POLY_SIGNATURE",
            "POLY_TIMESTAMP",
            "POLY_API_KEY",
            "POLY_PASSPHRASE",
        }

    def test_a_ordem_das_chaves_e_preservada(self):
        """`sort_keys` mudaria os bytes, e os bytes são a assinatura."""
        _, corpo = assinar_l2(
            _credenciais(), metodo="POST", caminho="/order", corpo={"z": 1, "a": 2}
        )

        assert corpo == b'{"z":1,"a":2}'

    def test_reserializar_com_json_padrao_produziria_outros_bytes(self):
        """O teste que mostra POR QUE o par existe.

        `json.dumps` no default insere espaço depois de `:` e de `,`. São
        dois bytes a mais numa ordem de duas chaves — suficiente para o HMAC
        dar outro digest.
        """
        corpo = {"b": 1, "a": 2}

        assert json.dumps(corpo).encode() != corpo_canonico(corpo)

    def test_corpo_ausente_e_vazio_e_nao_a_palavra_null(self):
        """`None` é "não há corpo", não é o JSON `null`.

        Assinar sobre b"null" e enviar corpo vazio é exatamente a divergência
        que este módulo existe para impedir.
        """
        assert corpo_canonico(None) == b""


class TestOHmac:
    def test_a_mensagem_e_timestamp_metodo_caminho_corpo_nessa_ordem(self):
        """`[VERIFICADO]` API_NOTES §3. A ordem importa: HMAC não é comutativo."""
        credenciais = _credenciais()
        cabecalhos, corpo = assinar_l2(
            credenciais,
            metodo="POST",
            caminho="/order",
            corpo={"a": 1},
            timestamp=1_700_000_000,
        )

        esperado = hmac.new(
            base64.urlsafe_b64decode(SEGREDO),
            b"1700000000POST/order" + corpo,
            hashlib.sha256,
        ).digest()

        assert cabecalhos["POLY_SIGNATURE"] == base64.urlsafe_b64encode(
            esperado
        ).decode()

    def test_o_segredo_e_decodificado_em_urlsafe_e_nao_no_alfabeto_padrao(self):
        """`[VERIFICADO]` API_NOTES §3, e a diferença é silenciosa.

        `-` e `_` não existem no alfabeto padrão. `b64decode` sem `validate`
        simplesmente DESCARTA os caracteres que não reconhece, então decodificar
        com o alfabeto errado não levanta nada: devolve outros bytes, e a
        assinatura sai errada em toda ordem até alguém investigar um 401.
        """
        assert "-" in SEGREDO, "o segredo do teste precisa exercitar o alfabeto"
        credenciais = _credenciais()

        assert credenciais.segredo_em_bytes() == base64.urlsafe_b64decode(SEGREDO)
        assert base64.b64decode(SEGREDO) != credenciais.segredo_em_bytes()

    def test_o_metodo_entra_em_maiuscula(self):
        """`post` e `POST` têm de produzir a mesma assinatura: o servidor vê
        o método normalizado, e um cliente que assine minúsculo é recusado."""
        maiusculo, _ = assinar_l2(
            _credenciais(), metodo="POST", caminho="/order", timestamp=1
        )
        minusculo, _ = assinar_l2(
            _credenciais(), metodo="post", caminho="/order", timestamp=1
        )

        assert maiusculo["POLY_SIGNATURE"] == minusculo["POLY_SIGNATURE"]

    def test_a_url_inteira_no_lugar_do_caminho_e_recusada(self):
        """Erro fácil de cometer e impossível de diagnosticar pela resposta.

        `[VERIFICADO]` API_NOTES §3: assina-se o *request path*. Assinar
        `https://clob.polymarket.com/order` produz assinatura que o servidor
        não reproduz, e a resposta é um 401 sem detalhe.
        """
        with pytest.raises(ErroDeAuth) as erro:
            assinar_l2(
                _credenciais(),
                metodo="POST",
                caminho="https://clob.polymarket.com/order",
            )

        assert "request path" in str(erro.value)


class TestAsCredenciais:
    @pytest.mark.parametrize(
        "vazio", ["api_key", "segredo", "passphrase", "endereco"]
    )
    def test_campo_vazio_falha_na_construcao_e_nao_no_servidor(self, vazio):
        """Falhar aqui nomeia o campo; falhar no servidor devolve 401 mudo."""
        with pytest.raises(ErroDeAuth) as erro:
            _credenciais(**{vazio: ""})

        assert vazio in str(erro.value)

    def test_segredo_invalido_falha_ao_assinar_com_motivo(self):
        credenciais = _credenciais(segredo="isto nao e base64!!!")

        with pytest.raises(ErroDeAuth) as erro:
            credenciais.segredo_em_bytes()

        assert "base64" in str(erro.value)

    def test_o_repr_nao_publica_o_segredo_nem_a_passphrase(self):
        """Um `log.exception` imprime o `repr` dos locais.

        Credencial em arquivo de log é lida por mais gente e guardada por mais
        tempo que a variável de ambiente de onde ela veio.
        """
        texto = repr(_credenciais())

        assert "segredo do pulsearb" not in texto
        assert SEGREDO not in texto
        assert "frase" not in texto
        assert "<oculto>" in texto


class TestOTypedDataDoL1:
    def test_os_campos_sao_os_verificados_no_API_NOTES(self):
        dado = typed_data_de_auth(endereco="0xabc", timestamp=7, nonce=3)

        assert dado["primaryType"] == "ClobAuth"
        assert dado["domain"] == {
            "name": "ClobAuthDomain",
            "version": "1",
            "chainId": CHAIN_ID_POLYGON,
        }
        assert [c["name"] for c in dado["types"]["ClobAuth"]] == [
            "address",
            "timestamp",
            "nonce",
            "message",
        ]

    def test_timestamp_e_string_e_nonce_e_inteiro(self):
        """EIP-712 assina o TIPO junto com o valor.

        Mandar o timestamp como `uint256` em vez de `string` produz outro
        hash, logo outra assinatura, logo recusa — e nada no nosso lado
        aponta para a causa.
        """
        dado = typed_data_de_auth(endereco="0xabc", timestamp=7, nonce=3)

        assert dado["message"]["timestamp"] == "7"
        assert isinstance(dado["message"]["timestamp"], str)
        assert dado["message"]["nonce"] == 3
        assert isinstance(dado["message"]["nonce"], int)

    def test_os_cabecalhos_l1_saem_do_assinador(self):
        class _Assinador:
            endereco = "0xdedicada"

            def assinar_typed_data(self, typed_data):
                assert typed_data["primaryType"] == "ClobAuth"
                return "0xassinatura"

        cabecalhos = cabecalhos_l1(_Assinador(), nonce=5, timestamp=99)

        assert cabecalhos == {
            "POLY_ADDRESS": "0xdedicada",
            "POLY_SIGNATURE": "0xassinatura",
            "POLY_TIMESTAMP": "99",
            "POLY_NONCE": "5",
        }


def test_eoa_e_o_tipo_de_assinatura_do_pulsearb():
    """O item 5.1 manda carteira DEDICADA; carteira proxy exigiria `funder`."""
    assert ASSINATURA_EOA == 0


class TestOSegredoMalformadoNaoViraChaveVazia:
    """Achado P2 do Codex no #52, e o meu próprio teste tinha o defeito.

    Eu documentei que `b64decode` descarta o que não reconhece — e continuei
    decodificando de forma permissiva. `"!!!!"` tem comprimento múltiplo de 4,
    então nem padding falta: decodificava para b"" **sem levantar nada**, e
    todo pedido passava a ser assinado com chave HMAC vazia. O sintoma seria
    uma fila de 401, sem nenhuma pista da causa.
    """

    @pytest.mark.parametrize(
        "malformado", ["!!!!", "====", "aaaa!!!!", "isto nao e base64!!!"]
    )
    def test_caractere_fora_do_alfabeto_levanta(self, malformado):
        with pytest.raises(ErroDeAuth):
            _credenciais(segredo=malformado).segredo_em_bytes()

    def test_segredo_que_decodifica_para_vazio_levanta(self):
        """Cobre o que a validação de alfabeto não pega: `"===="` e afins não
        têm caractere ilegal e ainda assim dão chave vazia."""
        with pytest.raises(ErroDeAuth) as erro:
            _credenciais(segredo="====").segredo_em_bytes()

        assert "vazio" in str(erro.value) or "base64" in str(erro.value)

    def test_segredo_sem_padding_continua_valido(self):
        """A validação não pode virar rigor inútil: segredo sem `=` no fim é
        comum e não é erro."""
        sem_padding = SEGREDO.rstrip("=")

        assert _credenciais(segredo=sem_padding).segredo_em_bytes() == (
            base64.urlsafe_b64decode(SEGREDO)
        )

    def test_a_traducao_de_alfabeto_nao_muda_o_resultado(self):
        """`validate` só existe em `b64decode`, então traduzimos `-_` para
        `+/` antes. O byte de saída tem de continuar sendo o do urlsafe."""
        assert _credenciais().segredo_em_bytes() == base64.urlsafe_b64decode(SEGREDO)
