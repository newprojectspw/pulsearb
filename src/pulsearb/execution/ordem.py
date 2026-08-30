"""Item 3.2, segunda metade — a struct EIP-712 da ordem e quem a assina.

TUDO AQUI FOI LIDO NA FONTE, NÃO DEDUZIDO
──────────────────────────────────────────
`[VERIFICADO]` contra `polymarket-client==0.6.0`, sdist baixado e lido:
`_internal/actions/orders/typed_data.py`, `.../limit.py`, `.../math.py` e
`.../context.py`. Os números abaixo são o que aquele código faz, não o que
seria razoável fazer — e a diferença importa, porque três deles são
contraintuitivos o bastante para um chute passar em teste próprio e falhar
no mercado.

**Os três que um chute erraria:**

1. **A versão do domínio da ordem é `"2"`, não `"1"`.** O domínio da
   *autenticação* (`ClobAuthDomain`, API_NOTES §3) é versão `"1"`, e a
   simetria é falsa. Versão errada muda o hash do domínio, logo a
   assinatura, logo a ordem é recusada — sem que nada aponte a causa.

2. **Em compra, `makerAmount` é USDC e `takerAmount` é SHARES.** O maker
   entrega colateral e recebe shares. Trocar os dois numa ordem de 5 shares a
   0,50 pediria 2,5 shares por 5 USDC — uma ordem válida, aceita, e com o
   dobro do preço pretendido.

3. **A conversão para unidades-base usa `ROUND_HALF_EVEN`**, e o
   arredondamento do valor tem DOIS passos (`_valor_arredondado`). Um
   `round()` ingênuo diverge em casos de meio-tick, e divergir na assinatura
   é recusa.

POR QUE ESCRITO AQUI E NÃO IMPORTADO DO SDK
────────────────────────────────────────────
Ver o cabeçalho de `auth.py` e o adendo do API_NOTES §1.4: o SDK exige
`websockets<16` e rebaixaria o pacote do caminho quente que gravou as 24 h do
M2. A criptografia continua vindo de biblioteca (`eth_account`); o que mora
aqui é a montagem dos campos.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, Decimal
from typing import Any

from pulsearb.execution.auth import ASSINATURA_EOA, CHAIN_ID_POLYGON

#: `[VERIFICADO]` `typed_data.py`: `_PROTOCOL_NAME` / `_PROTOCOL_VERSION`.
#: A versão é **"2"**. Ver o item 1 do cabeçalho.
NOME_DO_PROTOCOLO = "Polymarket CTF Exchange"
VERSAO_DO_PROTOCOLO = "2"

#: `[VERIFICADO]` API_NOTES §2 — os dois contratos de troca. O `neg_risk` do
#: mercado escolhe qual entra em `verifyingContract`, e usar o errado assina
#: uma ordem para outro contrato.
EXCHANGE_PADRAO = "0xE111180000d2663C0091e4f400237545B87B996B"
EXCHANGE_NEG_RISK = "0xe2222d279d744050d28e00520010520000310F59"

#: `[VERIFICADO]` `typed_data.py`: `_encode_side` — 0 é compra, 1 é venda.
LADO_COMPRA = 0
LADO_VENDA = 1

#: `[VERIFICADO]` `math.py`: `_COLLATERAL_DECIMALS`. USDC tem 6 casas.
CASAS_DO_COLATERAL = 6

#: `[VERIFICADO]` `context.py`: `_ROUNDING_BY_TICK`. Casas de arredondamento
#: por tick — (valor, preço, tamanho). Tick fora desta tabela é erro, e não
#: um default: um tick desconhecido significa que o mercado mudou de formato.
ARREDONDAMENTO_POR_TICK: dict[str, tuple[int, int, int]] = {
    "0.1": (3, 1, 2),
    "0.01": (4, 2, 2),
    "0.005": (5, 3, 2),
    "0.0025": (6, 4, 2),
    "0.001": (5, 3, 2),
    "0.0001": (6, 4, 2),
}

#: `[VERIFICADO]` `types.py`: `BYTES32_ZERO`. `metadata` e `builder` vazios.
BYTES32_ZERO = "0x" + "00" * 32

#: `[VERIFICADO]` `orders.py`: `_SALT_BITS = 53`, `secrets.randbits(53)`.
#:
#: São 53 bits e não 256, apesar de o campo ser `uint256`: 53 é o maior
#: inteiro exato de um float de dupla precisão, ou seja, o que sobrevive a
#: passar por um JSON lido em JavaScript. Gerar 256 bits aqui produziria um
#: salt que o servidor arredonda, e assinatura sobre salt arredondado não
#: confere.
#:
#: O salt é ALEATÓRIO por ordem, e é por isso que a idempotência do projeto
#: mora no `id_do_cliente` de `cliente.py` e não na assinatura: a mesma ordem
#: lógica assinada duas vezes produz dois salts, logo duas assinaturas, logo
#: duas ordens distintas para o contrato.
BITS_DO_SALT = 53


def novo_salt() -> int:
    """`[VERIFICADO]` `orders.py`: `_generate_salt`. `secrets`, não `random`.

    `random` é previsível a partir de saídas observadas, e salt previsível
    deixa terceiro montar antecipadamente a struct de uma ordem nossa.
    """
    return secrets.randbits(BITS_DO_SALT)


class ErroDeOrdem(ValueError):
    """A ordem não pode ser montada. Nunca é caso de tentar de novo igual."""


def _casas(valor: Decimal) -> int:
    """`[VERIFICADO]` `math.py`: `decimal_places`."""
    normalizado = valor.normalize()
    _, _, expoente = normalizado.as_tuple()
    if isinstance(expoente, str):  # NaN / Infinity
        return 0
    return max(0, -expoente)


def _arredondar(valor: Decimal, casas: int, modo: str) -> Decimal:
    """`[VERIFICADO]` `math.py`: `_round`. Não quantiza quem já cabe."""
    if _casas(valor) <= casas:
        return valor
    return valor.quantize(Decimal(10) ** -casas, rounding=modo)


def em_unidades_base(valor: Decimal) -> int:
    """`[VERIFICADO]` `math.py`: `parse_amount`.

    `ROUND_HALF_EVEN` e não `ROUND_HALF_UP`: em empate exato o Python padrão
    arredonda para o par, e é isso que o SDK faz. A diferença aparece em
    casos de meio-tick, e uma unidade-base de diferença é assinatura
    diferente, logo recusa.
    """
    escalado = (valor * (Decimal(10) ** CASAS_DO_COLATERAL)).quantize(
        Decimal(1), rounding=ROUND_HALF_EVEN
    )
    return int(escalado)


def _valor_arredondado(valor: Decimal, casas_do_valor: int) -> Decimal:
    """`[VERIFICADO]` `limit.py`: `_round_amount`. São DOIS passos.

    Sobe para `casas+4` e, se ainda não couber, desce para `casas`. Um
    `round(valor, casas)` direto diverge — e divergir aqui é recusa.
    """
    if _casas(valor) <= casas_do_valor:
        return valor
    valor = _arredondar(valor, casas_do_valor + 4, ROUND_CEILING)
    if _casas(valor) > casas_do_valor:
        valor = _arredondar(valor, casas_do_valor, ROUND_FLOOR)
    return valor


def valores_da_ordem(
    *, preco: Decimal, tamanho: Decimal, compra: bool, tick: str
) -> tuple[int, int]:
    """`(makerAmount, takerAmount)` em unidades-base.

    `[VERIFICADO]` `limit.py`: `_compute_limit_order_amounts`.

    **A orientação é o item 2 do cabeçalho.** Em COMPRA o maker entrega USDC
    e recebe shares, então `makerAmount` é dinheiro e `takerAmount` é
    quantidade. Em VENDA é o contrário. Trocar os dois produz uma ordem
    válida com o preço invertido.
    """
    config = ARREDONDAMENTO_POR_TICK.get(tick)
    if config is None:
        raise ErroDeOrdem(
            f"tick {tick!r} fora da tabela verificada do SDK "
            f"({sorted(ARREDONDAMENTO_POR_TICK)}). Tick desconhecido significa "
            "que o mercado mudou de formato — assinar por default seria "
            "arredondar por adivinhacao."
        )
    casas_do_valor, _, casas_do_tamanho = config

    if compra:
        taker = _arredondar(tamanho, casas_do_tamanho, ROUND_FLOOR)
        maker = _valor_arredondado(taker * preco, casas_do_valor)
        return em_unidades_base(maker), em_unidades_base(taker)

    maker = _arredondar(tamanho, casas_do_tamanho, ROUND_FLOOR)
    taker = _valor_arredondado(maker * preco, casas_do_valor)
    return em_unidades_base(maker), em_unidades_base(taker)


@dataclass(frozen=True)
class OrdemNaoAssinada:
    """Os onze campos da struct, e nada além deles.

    `[VERIFICADO]` `typed_data.py`: `_ORDER_FIELDS`. A ordem dos campos é
    parte do hash — `Order(uint256 salt,address maker,...)` — então mexer
    aqui muda a assinatura.
    """

    salt: int
    maker: str
    signer: str
    token_id: str
    maker_amount: int
    taker_amount: int
    compra: bool
    #: Instante de CRIAÇÃO, em milissegundos. `[VERIFICADO]` `orders.py`:
    #: `timestamp=_current_timestamp_ms()`. O nome engana de duas formas:
    #: não é a expiração (essa é `expiracao`, campo separado que NÃO entra na
    #: struct assinada), e é em MILISSEGUNDOS, não segundos.
    timestamp_ms: int = 0
    #: Expiração em epoch; `0` = não expira. Vai no corpo do fio, mas **não**
    #: é assinada — não está em `_ORDER_FIELDS`.
    expiracao: int = 0
    signature_type: int = ASSINATURA_EOA
    metadata: str = BYTES32_ZERO
    builder: str = BYTES32_ZERO
    chain_id: int = CHAIN_ID_POLYGON
    neg_risk: bool = False

    @property
    def exchange(self) -> str:
        return EXCHANGE_NEG_RISK if self.neg_risk else EXCHANGE_PADRAO

    @property
    def lado(self) -> int:
        return LADO_COMPRA if self.compra else LADO_VENDA


#: `[VERIFICADO]` `typed_data.py`: `_EIP712_DOMAIN_FIELDS` e `_ORDER_FIELDS`.
CAMPOS_DO_DOMINIO = [
    {"name": "name", "type": "string"},
    {"name": "version", "type": "string"},
    {"name": "chainId", "type": "uint256"},
    {"name": "verifyingContract", "type": "address"},
]
CAMPOS_DA_ORDEM = [
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
]


def typed_data_da_ordem(ordem: OrdemNaoAssinada) -> dict[str, Any]:
    """O typed data EIP-712 da ordem, campo a campo como o SDK monta.

    Duas armadilhas no campo `timestamp`, e eu caí nas duas antes de ler a
    fonte: ele é o instante de **criação em milissegundos**, e **não** a
    expiração. A expiração é campo separado, vai no corpo do fio e não é
    assinada. Preencher `timestamp` com `time.time()` (segundos) manda um
    número mil vezes menor do que o servidor espera.
    """
    return {
        "types": {"EIP712Domain": CAMPOS_DO_DOMINIO, "Order": CAMPOS_DA_ORDEM},
        "primaryType": "Order",
        "domain": {
            "name": NOME_DO_PROTOCOLO,
            "version": VERSAO_DO_PROTOCOLO,
            "chainId": ordem.chain_id,
            "verifyingContract": ordem.exchange,
        },
        "message": {
            "salt": ordem.salt,
            "maker": ordem.maker,
            "signer": ordem.signer,
            "tokenId": int(ordem.token_id),
            "makerAmount": ordem.maker_amount,
            "takerAmount": ordem.taker_amount,
            "side": ordem.lado,
            "signatureType": ordem.signature_type,
            "timestamp": ordem.timestamp_ms,
            "metadata": ordem.metadata,
            "builder": ordem.builder,
        },
    }


class AssinadorLocal:
    """Assina com uma chave privada em memória, via `eth_account`.

    A CRIPTOGRAFIA NÃO É NOSSA, e não deve ser. `eth_account` é a mesma
    biblioteca que o SDK oficial usa por baixo, e é a única coisa que ele
    fazia que valia importar. O que este projeto escreve é a montagem dos
    campos — que é onde os erros de verdade acontecem, e que está travada
    contra a fonte em `tests/test_m4_struct_da_ordem.py`.

    A CHAVE:

    - entra por `PULSEARB_CHAVE_PRIVADA` e não por arquivo de configuração,
      porque `config.yaml` é versionado e `.env` não;
    - nunca aparece em `repr`, em log ou em traceback — ver `__repr__`;
    - não é atributo público: quem tem a instância pode assinar, mas não pode
      ler a chave por engano num `asdict` ou num dump de estado.

    Isto NÃO liga o modo LIVE. A trava tripla do item 3.4
    (`risk/autorizacao.py`) continua sendo a única porta, e `escolher_executor`
    continua recusando — ter com que assinar não é ter autorização para
    enviar.
    """

    #: De onde a chave sai. Variável de ambiente, nunca o `config.yaml`.
    ENV_DA_CHAVE = "PULSEARB_CHAVE_PRIVADA"

    def __init__(self, chave_privada: str) -> None:
        try:
            from eth_account import Account
        except ImportError as erro:  # pragma: no cover - dependência declarada
            raise ErroDeOrdem(
                "eth-account nao esta instalado. Ele e a dependencia de "
                "assinatura do M4 (API_NOTES §1.4): `pip install -e .`"
            ) from erro

        if not chave_privada:
            raise ErroDeOrdem(
                f"chave privada vazia. Ela vem de {self.ENV_DA_CHAVE}, e nunca "
                "do config.yaml — que e versionado."
            )
        try:
            self._conta = Account.from_key(chave_privada)
        except (ValueError, TypeError) as erro:
            # A mensagem NÃO repete a chave: um erro de formato num log
            # publicaria a chave inteira no arquivo.
            raise ErroDeOrdem(
                f"chave privada invalida ({type(erro).__name__}) — a chave nao "
                "e repetida aqui de proposito"
            ) from erro

    @classmethod
    def do_ambiente(cls, env: dict[str, str] | None = None) -> AssinadorLocal:
        """Constrói a partir de `PULSEARB_CHAVE_PRIVADA`."""
        import os

        ambiente = os.environ if env is None else env
        return cls(ambiente.get(cls.ENV_DA_CHAVE, ""))

    @property
    def endereco(self) -> str:
        return self._conta.address

    def assinar_typed_data(self, typed_data: dict[str, Any]) -> str:
        """Assinatura EIP-712 em hex `0x…`. Serve para o L1 e para a ordem.

        `sign_typed_data(full_message=...)` é a mesma chamada do SDK
        (`l1_auth.py`, `combo_rfq.py`). O prefixo `0x` também: em
        `eth-account` recente `.hex()` já não o inclui, e o SDK repõe com
        `raw if raw.startswith("0x") else "0x" + raw`. Assinatura sem prefixo
        é recusada, e a recusa não diz que o problema é o prefixo.
        """
        assinada = self._conta.sign_typed_data(full_message=typed_data)
        bruto = assinada.signature.hex()
        return bruto if bruto.startswith("0x") else "0x" + bruto

    def assinar_ordem(self, ordem: OrdemNaoAssinada) -> str:
        """A assinatura da struct da ordem, pronta para o corpo do envio."""
        return self.assinar_typed_data(typed_data_da_ordem(ordem))

    def __repr__(self) -> str:
        """Só o endereço. O endereço é público; a chave não.

        Um `log.exception` imprime o `repr` dos locais, e chave privada em
        arquivo de log é a perda total do capital da carteira.
        """
        return f"AssinadorLocal(endereco={self.endereco})"
