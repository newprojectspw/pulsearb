"""3.2, segunda metade — a struct EIP-712 da ordem.

DE ONDE VÊM OS NÚMEROS ESPERADOS
─────────────────────────────────
Não foram calculados à mão nem inferidos da documentação: saíram de uma
**conferência diferencial** contra o `polymarket-client==0.6.0`. O sdist foi
baixado, a fonte carregada direto do disco (sem instalar, para não rebaixar o
`websockets` do caminho quente — ver API_NOTES §1.4), e cada caso abaixo foi
comparado com o resultado de `_compute_limit_order_amounts` e
`build_order_typed_data` do próprio SDK. Os sete casos e o typed data bateram
byte a byte.

O SDK não entra na suíte porque não é dependência do projeto. Então os
valores que ele produziu ficam aqui como referência fixa: se alguém mexer no
arredondamento, o teste falha contra o número que o SDK produziria — que é o
número que o contrato espera.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from pulsearb.execution.ordem import (
    ARREDONDAMENTO_POR_TICK,
    BITS_DO_SALT,
    EXCHANGE_NEG_RISK,
    EXCHANGE_PADRAO,
    LADO_COMPRA,
    LADO_VENDA,
    NOME_DO_PROTOCOLO,
    VERSAO_DO_PROTOCOLO,
    ErroDeOrdem,
    OrdemNaoAssinada,
    em_unidades_base,
    novo_salt,
    typed_data_da_ordem,
    valores_da_ordem,
)


def _ordem(**ajustes):
    base = {
        "salt": 123456789,
        "maker": "0xAAaa",
        "signer": "0xBBbb",
        "token_id": "42",
        "maker_amount": 2500000,
        "taker_amount": 5000000,
        "compra": True,
        "timestamp_ms": 1756000000000,
    }
    base.update(ajustes)
    return OrdemNaoAssinada(**base)


class TestOsValores:
    """Os sete casos conferidos contra o SDK, com o resultado dele."""

    @pytest.mark.parametrize(
        ("preco", "tamanho", "compra", "tick", "esperado"),
        [
            ("0.50", "5", True, "0.01", (2500000, 5000000)),
            ("0.37", "13.7", True, "0.01", (5069000, 13700000)),
            ("0.335", "7.77", True, "0.005", (2602950, 7770000)),
            ("0.9999", "101.05", True, "0.0001", (101039895, 101050000)),
            ("0.50", "5", False, "0.01", (5000000, 2500000)),
            ("0.63", "33.333", False, "0.01", (33330000, 20997900)),
            ("0.1", "9.99", False, "0.1", (9990000, 999000)),
        ],
    )
    def test_bate_com_o_SDK(self, preco, tamanho, compra, tick, esperado):
        assert (
            valores_da_ordem(
                preco=Decimal(preco),
                tamanho=Decimal(tamanho),
                compra=compra,
                tick=tick,
            )
            == esperado
        )

    def test_em_COMPRA_o_maker_e_dinheiro_e_o_taker_e_share(self):
        """A armadilha nº 2 do módulo, e a mais cara.

        O maker entrega colateral e recebe shares. Trocar os dois numa ordem
        de 5 shares a 0,50 pediria 2,5 shares por 5 USDC — uma ordem VÁLIDA,
        aceita pelo contrato, com o dobro do preço pretendido.
        """
        maker, taker = valores_da_ordem(
            preco=Decimal("0.50"), tamanho=Decimal("5"), compra=True, tick="0.01"
        )

        assert maker == 2_500_000, "USDC: 5 shares x 0,50 = 2,50"
        assert taker == 5_000_000, "shares: 5"

    def test_em_VENDA_a_orientacao_inverte(self):
        maker, taker = valores_da_ordem(
            preco=Decimal("0.50"), tamanho=Decimal("5"), compra=False, tick="0.01"
        )

        assert maker == 5_000_000, "shares: 5"
        assert taker == 2_500_000, "USDC: 2,50"

    def test_tick_desconhecido_levanta_em_vez_de_assumir_um_default(self):
        """Tick fora da tabela significa que o mercado mudou de formato.
        Arredondar por default seria arredondar por adivinhação."""
        with pytest.raises(ErroDeOrdem) as erro:
            valores_da_ordem(
                preco=Decimal("0.5"), tamanho=Decimal("5"), compra=True, tick="0.02"
            )

        assert "0.02" in str(erro.value)

    def test_a_tabela_de_arredondamento_e_a_do_SDK(self):
        """`[VERIFICADO]` `context.py`: `_ROUNDING_BY_TICK`."""
        assert ARREDONDAMENTO_POR_TICK == {
            "0.1": (3, 1, 2),
            "0.01": (4, 2, 2),
            "0.005": (5, 3, 2),
            "0.0025": (6, 4, 2),
            "0.001": (5, 3, 2),
            "0.0001": (6, 4, 2),
        }


class TestAConversaoParaUnidadesBase:
    def test_usdc_tem_seis_casas(self):
        assert em_unidades_base(Decimal("1")) == 1_000_000
        assert em_unidades_base(Decimal("2.5")) == 2_500_000

    def test_o_empate_arredonda_para_o_PAR_e_nao_para_cima(self):
        """`ROUND_HALF_EVEN`, não `ROUND_HALF_UP` — é o que o SDK faz.

        Uma unidade-base de diferença é outra assinatura, logo recusa. O
        `round()` embutido do Python também é half-even; `ROUND_HALF_UP`
        seria o engano natural de quem vem de outra linguagem.
        """
        assert em_unidades_base(Decimal("0.0000005")) == 0
        assert em_unidades_base(Decimal("0.0000015")) == 2


class TestOTypedData:
    def test_a_versao_do_dominio_da_ordem_e_2_e_nao_1(self):
        """A armadilha nº 1. O domínio da AUTENTICAÇÃO (`ClobAuthDomain`) é
        versão "1", e a simetria é falsa: a da ordem é "2". Versão errada muda
        o hash do domínio, logo a assinatura, logo a ordem é recusada — sem
        que nada aponte a causa."""
        dado = typed_data_da_ordem(_ordem())

        assert VERSAO_DO_PROTOCOLO == "2"
        assert dado["domain"]["version"] == "2"
        assert dado["domain"]["name"] == NOME_DO_PROTOCOLO

    def test_a_estrutura_inteira_bate_com_a_do_SDK(self):
        """O typed data completo, como `build_order_typed_data` devolveu na
        conferência diferencial."""
        assert typed_data_da_ordem(_ordem()) == {
            "types": {
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "version", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                    {"name": "verifyingContract", "type": "address"},
                ],
                "Order": [
                    {"name": "salt", "type": "uint256"},
                    {"name": "maker", "type": "address"},
                    {"name": "signer", "type": "address"},
                    {"name": "tokenId", "type": "uint256"},
                    {"name": "makerAmount", "type": "uint256"},
                    {"name": "takerAmount", "type": "uint256"},
                    {"name": "side", "type": "uint8"},
                    {"name": "signatureType", "type": "uint8"},
                    {"name": "timestamp", "type": "uint256"},
                    {"name": "metadata", "type": "bytes32"},
                    {"name": "builder", "type": "bytes32"},
                ],
            },
            "primaryType": "Order",
            "domain": {
                "name": "Polymarket CTF Exchange",
                "version": "2",
                "chainId": 137,
                "verifyingContract": EXCHANGE_PADRAO,
            },
            "message": {
                "salt": 123456789,
                "maker": "0xAAaa",
                "signer": "0xBBbb",
                "tokenId": 42,
                "makerAmount": 2500000,
                "takerAmount": 5000000,
                "side": 0,
                "signatureType": 0,
                "timestamp": 1756000000000,
                "metadata": "0x" + "00" * 32,
                "builder": "0x" + "00" * 32,
            },
        }

    def test_o_tokenId_vai_como_INTEIRO(self):
        """O campo é `uint256`. Mandá-lo como string produz outro hash."""
        dado = typed_data_da_ordem(_ordem(token_id="99"))

        assert dado["message"]["tokenId"] == 99
        assert isinstance(dado["message"]["tokenId"], int)

    def test_o_timestamp_e_a_criacao_em_MILISSEGUNDOS_e_nao_a_expiracao(self):
        """Eu tinha escrito o contrário antes de ler a fonte.

        `[VERIFICADO]` `orders.py`: `timestamp=_current_timestamp_ms()`. A
        expiração é campo SEPARADO, vai no corpo do fio e **não** está em
        `_ORDER_FIELDS` — não é assinada. Preencher `timestamp` com
        `time.time()` mandaria um número mil vezes menor que o esperado.
        """
        dado = typed_data_da_ordem(_ordem(timestamp_ms=1756000000000, expiracao=999))

        assert dado["message"]["timestamp"] == 1756000000000
        assert "expiration" not in dado["message"]
        assert [c["name"] for c in dado["types"]["Order"]].count("timestamp") == 1

    @pytest.mark.parametrize(
        ("compra", "lado"), [(True, LADO_COMPRA), (False, LADO_VENDA)]
    )
    def test_o_lado_e_zero_para_compra_e_um_para_venda(self, compra, lado):
        """`[VERIFICADO]` `typed_data.py`: `_encode_side`."""
        assert typed_data_da_ordem(_ordem(compra=compra))["message"]["side"] == lado
        assert (LADO_COMPRA, LADO_VENDA) == (0, 1)

    def test_neg_risk_muda_o_contrato_verificador(self):
        """Assinar para o contrato errado produz uma ordem que o outro não
        reconhece."""
        padrao = typed_data_da_ordem(_ordem(neg_risk=False))
        neg = typed_data_da_ordem(_ordem(neg_risk=True))

        assert padrao["domain"]["verifyingContract"] == EXCHANGE_PADRAO
        assert neg["domain"]["verifyingContract"] == EXCHANGE_NEG_RISK

    def test_a_ordem_dos_campos_faz_parte_do_hash(self):
        """`Order(uint256 salt,address maker,...)` é hasheado como texto.
        Reordenar os campos muda o `_ORDER_TYPE_HASH` e invalida tudo."""
        nomes = [c["name"] for c in typed_data_da_ordem(_ordem())["types"]["Order"]]

        assert nomes == [
            "salt",
            "maker",
            "signer",
            "tokenId",
            "makerAmount",
            "takerAmount",
            "side",
            "signatureType",
            "timestamp",
            "metadata",
            "builder",
        ]


class TestOSalt:
    def test_sao_53_bits_e_nao_256(self):
        """53 é o maior inteiro exato de um float de dupla precisão — o que
        sobrevive a um JSON lido em JavaScript. 256 bits produziriam um salt
        que o servidor arredonda, e assinatura sobre salt arredondado não
        confere."""
        assert BITS_DO_SALT == 53
        assert all(novo_salt() < 2**53 for _ in range(200))

    def test_o_salt_muda_a_cada_ordem(self):
        """E é por isso que a idempotência mora no `id_do_cliente`, e não na
        assinatura: a mesma ordem lógica assinada duas vezes vira duas ordens
        distintas para o contrato."""
        assert len({novo_salt() for _ in range(200)}) > 190


class TestOAssinador:
    """A assinatura conferida contra o SDK, com chave fixa.

    A chave abaixo é `0x11…11` — um valor de teste conhecido, jamais uma
    carteira real. A assinatura esperada saiu da conferência diferencial:
    `Account.sign_typed_data(full_message=build_order_typed_data(...))` do
    próprio SDK produziu exatamente estes bytes.
    """

    CHAVE = "0x" + "11" * 32
    ENDERECO = "0x19E7E376E7C213B7E7e7e46cc70A5dD086DAff2A"
    ASSINATURA = (
        "0x1ae34f0a78685e7449fa768c3f828c36c8ca29f82fcb93bb5ec285a5817452ac"
        "2c81b137fdd2e6ab0eeeb8f31e84c814d3255fae025d36d770cc604b296bde881c"
    )

    def _assinador(self):
        from pulsearb.execution.ordem import AssinadorLocal

        return AssinadorLocal(self.CHAVE)

    def _assinavel(self, **ajustes):
        """Ordem com endereços REAIS.

        `_ordem()` usa `0xAAaa`/`0xBBbb`, que servem para comparar o dicionário
        do typed data mas não são endereços de 20 bytes — o codificador ABI
        recusa, e recusar é o comportamento certo dele.
        """
        ajustes.setdefault("maker", self.ENDERECO)
        ajustes.setdefault("signer", self.ENDERECO)
        return _ordem(**ajustes)

    def test_o_endereco_sai_da_chave(self):
        assert self._assinador().endereco == self.ENDERECO

    def test_a_assinatura_bate_com_a_do_SDK(self):
        """O teste que fecha o 3.2: mesma chave, mesma ordem, mesmos bytes."""
        assinador = self._assinador()
        assert assinador.assinar_ordem(self._assinavel()) == self.ASSINATURA

    def test_a_assinatura_vem_com_prefixo_0x(self):
        """Em `eth-account` recente `.hex()` já não inclui o prefixo, e o SDK
        o repõe. Assinatura sem `0x` é recusada, e a recusa não diz que o
        problema é o prefixo."""
        assinatura = self._assinador().assinar_ordem(self._assinavel())

        assert assinatura.startswith("0x")
        assert len(assinatura) == 132  # 0x + 65 bytes

    def test_mudar_UM_campo_muda_a_assinatura(self):
        """Cada campo entra no hash. Se algum não entrasse, a struct estaria
        errada e a ordem assinada não seria a ordem pretendida."""
        assinador = self._assinador()
        base = assinador.assinar_ordem(self._assinavel())

        for mudanca in (
            {"salt": 987654321},
            {"maker_amount": 2500001},
            {"taker_amount": 5000001},
            {"compra": False},
            {"token_id": "43"},
            {"timestamp_ms": 1756000000001},
            {"neg_risk": True},
        ):
            assert assinador.assinar_ordem(self._assinavel(**mudanca)) != base, mudanca

    def test_a_expiracao_NAO_muda_a_assinatura(self):
        """Ela não está em `_ORDER_FIELDS`: vai no corpo do fio e não é
        assinada. O teste trava a assimetria para que ninguém a "conserte"."""
        assinador = self._assinador()

        assert assinador.assinar_ordem(
            self._assinavel(expiracao=0)
        ) == assinador.assinar_ordem(self._assinavel(expiracao=999999))

    def test_o_repr_nao_publica_a_chave(self):
        """Chave privada em arquivo de log é a perda total do capital da
        carteira. Um `log.exception` imprime o `repr` dos locais."""
        texto = repr(self._assinador())

        assert "11" * 32 not in texto
        assert self.ENDERECO in texto

    def test_chave_vazia_falha_dizendo_de_onde_ela_deveria_vir(self):
        from pulsearb.execution.ordem import AssinadorLocal

        with pytest.raises(ErroDeOrdem) as erro:
            AssinadorLocal("")

        assert "PULSEARB_CHAVE_PRIVADA" in str(erro.value)

    def test_chave_invalida_NAO_e_repetida_na_mensagem(self):
        """A mensagem de erro vira log, e log com chave é o mesmo vazamento."""
        from pulsearb.execution.ordem import AssinadorLocal

        chave_ruim = "0xdeadbeef" + "99" * 20

        with pytest.raises(ErroDeOrdem) as erro:
            AssinadorLocal(chave_ruim)

        assert chave_ruim not in str(erro.value)

    def test_do_ambiente_le_a_variavel_certa(self):
        from pulsearb.execution.ordem import AssinadorLocal

        assinador = AssinadorLocal.do_ambiente({"PULSEARB_CHAVE_PRIVADA": self.CHAVE})

        assert assinador.endereco == self.ENDERECO

    def test_sem_a_variavel_falha_fechado(self):
        from pulsearb.execution.ordem import AssinadorLocal

        with pytest.raises(ErroDeOrdem):
            AssinadorLocal.do_ambiente({})


class TestOTimestampNaoTemDefault:
    """Achado P2 do Codex no #52.

    Um default de `0` assinaria `timestamp: 0` — uma ordem com aparência
    válida que o servidor recusa, e nada no nosso lado apontaria o campo.
    """

    def test_esquecer_o_timestamp_e_TypeError_na_construcao(self):
        """Onde o erro custa menos: na construção, não numa recusa remota."""
        with pytest.raises(TypeError) as erro:
            OrdemNaoAssinada(
                salt=1,
                maker="0xA",
                signer="0xB",
                token_id="42",
                maker_amount=1,
                taker_amount=1,
                compra=True,
            )

        assert "timestamp_ms" in str(erro.value)

    def test_agora_em_ms_e_em_MILISSEGUNDOS(self):
        """`int(time.time())` mandaria um número mil vezes menor. A função
        existe para que a conversão apareça uma vez só."""
        import time

        from pulsearb.execution.ordem import agora_em_ms

        segundos = time.time()
        medido = agora_em_ms()

        assert abs(medido - segundos * 1000) < 5000
        assert medido > 1_000_000_000_000
