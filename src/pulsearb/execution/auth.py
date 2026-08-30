"""Item 3.2 — a autenticação do CLOB, nos três níveis.

O que cada nível serve, e por que isso importa para o PULSEARB
(API_NOTES §3, tudo `[VERIFICADO]` contra o SDK 0.6.0):

- **L0**, sem auth: livro, preços, tick size, Gamma, RTDS. **É tudo que SIM e
  SHADOW precisam** — e é por isso que o M1–M3 inteiro rodou sem nunca ter
  visto uma chave privada.
- **L1**, EIP-712 com a chave privada: serve **só** para criar ou derivar as
  credenciais de API. Não assina ordem.
- **L2**, HMAC-SHA256 com as credenciais: é o que assina **cada ordem**.

A DECISÃO DE DEPENDÊNCIA, que mudou em relação à §1.4
─────────────────────────────────────────────────────
A §1.4 registrou que o SDK oficial entraria no M4 para "assinatura EIP-712,
nonce, auth L1/L2 — coisas que não se deve reimplementar". A intenção
continua certa; o pacote, não. Medido nesta máquina:

    polymarket-client==0.6.0 exige websockets<16
    pulsearb                 fixa   websockets==17.0.1

Instalar o SDK **rebaixa o `websockets` do caminho quente** — o mesmo pacote
que gravou as 24 h do M2. Trocar a biblioteca de socket embaixo do recorder
para ganhar uma função de assinatura é pagar caro no lugar errado: a
população que o backtest leu passaria a vir de outra pilha de rede, e a
comparação SHADOW × backtest é justamente o que o M2 existe para fazer.

O que de fato não se deve reimplementar é a **criptografia**, e ela não mora
no SDK: mora no `eth-account`, que o próprio SDK usa por baixo e que **não
depende de websockets**. Então é ele a dependência, e o que sobra aqui é
serialização — HMAC de biblioteca padrão e um dicionário de typed data cujos
campos estão verificados campo a campo no API_NOTES §3.

O DETALHE QUE QUEBRA A ASSINATURA EM SILÊNCIO
──────────────────────────────────────────────
`[VERIFICADO]` API_NOTES §3: o corpo assinado no L2 tem de ser o **body
pré-serializado exato** que vai no fio. Reserializar o JSON entre assinar e
enviar troca um espaço, uma ordem de chave ou um separador — e o servidor
recusa com uma mensagem que não diz nada sobre isso.

Por isso `assinar_l2` **não** devolve só cabeçalhos: devolve o par
(cabeçalhos, corpo em bytes), e é esse corpo que precisa ir no fio. Quem
chama não recebe a opção de reserializar, porque a opção é a armadilha.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any, Protocol

#: `[VERIFICADO]` API_NOTES §3 — domínio do typed data do L1.
DOMINIO_DE_AUTH = "ClobAuthDomain"
VERSAO_DO_DOMINIO = "1"
TIPO_PRIMARIO_DE_AUTH = "ClobAuth"

#: `[VERIFICADO]` API_NOTES §2 — Polygon mainnet.
CHAIN_ID_POLYGON = 137

#: `[VERIFICADO]` API_NOTES §3 — a mensagem que o L1 assina, literal.
MENSAGEM_DE_AUTH = "This message attests that I control the given wallet"

#: `[VERIFICADO]` API_NOTES §3 — tipos de assinatura de carteira.
#: EOA é o caminho do PULSEARB: o item 5.1 manda carteira DEDICADA, e carteira
#: proxy exigiria também o `funder`.
ASSINATURA_EOA = 0


#: A tradução do alfabeto urlsafe para o padrão. Existe porque
#: `urlsafe_b64decode` **não aceita `validate`** — só `b64decode` aceita — e é
#: a validação que impede o segredo malformado de virar chave HMAC vazia.
_PARA_ALFABETO_PADRAO = str.maketrans("-_", "+/")


def _normalizado(texto: str) -> bytes:
    """O segredo no alfabeto padrão e com padding, pronto para validação.

    O padding que falta é reposto: segredo sem `=` no fim é comum e não é erro,
    mas `validate=True` exige comprimento múltiplo de 4. O que NÃO se repõe é
    caractere fora do alfabeto — esse é o que precisa levantar.
    """
    dado = texto.translate(_PARA_ALFABETO_PADRAO).encode("ascii", errors="strict")
    falta = (-len(dado)) % 4
    return dado + b"=" * falta


class ErroDeAuth(RuntimeError):
    """Falha de autenticação. Nunca é motivo para tentar de novo às cegas."""


@dataclass(frozen=True)
class CredenciaisL2:
    """As credenciais de API que o L1 deriva e o L2 usa.

    `segredo` é base64 **urlsafe** (`[VERIFICADO]` API_NOTES §3). Decodificar
    com o alfabeto padrão produz bytes diferentes sempre que o segredo tiver
    `-` ou `_`, e a assinatura sai errada sem erro nenhum até o servidor
    recusar.
    """

    api_key: str
    segredo: str
    passphrase: str
    endereco: str

    def __post_init__(self) -> None:
        faltando = [
            nome
            for nome in ("api_key", "segredo", "passphrase", "endereco")
            if not getattr(self, nome)
        ]
        if faltando:
            raise ErroDeAuth(
                f"credenciais L2 incompletas: {', '.join(faltando)} — "
                "assinar com campo vazio produz recusa do servidor sem dizer "
                "qual campo faltou"
            )

    def segredo_em_bytes(self) -> bytes:
        """Decodifica com validação ESTRITA. Achado P2 do Codex no #52.

        `urlsafe_b64decode` é permissivo: descarta em silêncio tudo que não
        está no alfabeto. `"!!!!"` tem comprimento múltiplo de 4, então nem
        padding falta — decodifica para b"" **sem levantar nada**, e todo
        pedido passa a ser assinado com chave HMAC vazia. O sintoma seria uma
        fila de 401 do servidor, sem nenhuma pista de que o segredo é que
        estava malformado.

        `validate=True` recusa o caractere estranho, e o teste do resultado
        vazio cobre o resto (`""` e `"===="` decodificam para b"" sem
        caractere ilegal nenhum).
        """
        try:
            bruto = base64.b64decode(_normalizado(self.segredo), validate=True)
        except (ValueError, TypeError) as erro:
            raise ErroDeAuth(
                f"segredo da API nao e base64 urlsafe valido: {erro}"
            ) from erro
        if not bruto:
            raise ErroDeAuth(
                "segredo da API decodifica para vazio — assinar com chave HMAC "
                "vazia produz 401 em toda ordem, sem dizer que a causa e o segredo"
            )
        return bruto

    def __repr__(self) -> str:
        """Segredo e passphrase NUNCA aparecem em log ou traceback.

        Um `repr` completo num `log.exception` publica a credencial no
        arquivo de log, que é lido por mais gente e guardado por mais tempo
        que a variável de ambiente de onde ela veio.
        """
        return (
            f"CredenciaisL2(api_key={self.api_key[:4]}…, endereco={self.endereco}, "
            "segredo=<oculto>, passphrase=<oculto>)"
        )


def corpo_canonico(corpo: Any | None) -> bytes:
    """A ÚNICA serialização aceita — a que se assina e a que vai no fio.

    Separadores sem espaço e chaves na ordem de inserção, para que assinar e
    enviar não possam divergir. `None` vira b"" e não b"null": corpo ausente e
    corpo nulo são coisas diferentes para a assinatura.
    """
    if corpo is None:
        return b""
    return json.dumps(corpo, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def assinar_l2(
    credenciais: CredenciaisL2,
    *,
    metodo: str,
    caminho: str,
    corpo: Any | None = None,
    timestamp: int | None = None,
) -> tuple[dict[str, str], bytes]:
    """Cabeçalhos L2 **e o corpo exato** que os acompanha.

    `[VERIFICADO]` API_NOTES §3: HMAC-SHA256 sobre
    `timestamp + method + request_path + body`, segredo decodificado em base64
    urlsafe, resultado recodificado em base64 urlsafe.

    Devolve o par de propósito — ver o cabeçalho do módulo. O corpo devolvido
    é o que tem de ir no fio, byte a byte; reserializar quebra a assinatura.
    """
    if not caminho.startswith("/"):
        raise ErroDeAuth(
            f"caminho precisa ser o request path absoluto, comecando em '/': {caminho!r}. "
            "Assinar a URL inteira produz uma assinatura que o servidor nao reproduz."
        )

    agora = int(time.time()) if timestamp is None else timestamp
    bytes_do_corpo = corpo_canonico(corpo)
    mensagem = (
        f"{agora}{metodo.upper()}{caminho}".encode() + bytes_do_corpo
    )
    digest = hmac.new(
        credenciais.segredo_em_bytes(), mensagem, hashlib.sha256
    ).digest()

    cabecalhos = {
        "POLY_ADDRESS": credenciais.endereco,
        "POLY_SIGNATURE": base64.urlsafe_b64encode(digest).decode("ascii"),
        "POLY_TIMESTAMP": str(agora),
        "POLY_API_KEY": credenciais.api_key,
        "POLY_PASSPHRASE": credenciais.passphrase,
    }
    return cabecalhos, bytes_do_corpo


class AssinadorL1(Protocol):
    """O contrato mínimo para assinar typed data EIP-712.

    Protocolo, e não import direto do `eth_account`: mantém a chave privada
    fora do tipo, deixa o teste passar um dublê sem nenhuma criptografia, e
    permite trocar a implementação (chave em cofre, HSM) sem tocar aqui.
    """

    @property
    def endereco(self) -> str:
        """O endereço da carteira, em hex `0x…`."""
        ...

    def assinar_typed_data(self, typed_data: dict[str, Any]) -> str:
        """Assinatura EIP-712 em hex `0x…`."""
        ...


def typed_data_de_auth(
    *,
    endereco: str,
    timestamp: int,
    nonce: int = 0,
    chain_id: int = CHAIN_ID_POLYGON,
) -> dict[str, Any]:
    """O typed data do L1, campo a campo como o API_NOTES §3 registra.

    `timestamp` vai como **string** e `nonce` como **uint256** — não é
    capricho de formatação: EIP-712 assina o tipo junto com o valor, e
    trocar `string` por `uint256` produz um hash diferente, logo uma
    assinatura que o servidor não reconhece.
    """
    return {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
            ],
            TIPO_PRIMARIO_DE_AUTH: [
                {"name": "address", "type": "address"},
                {"name": "timestamp", "type": "string"},
                {"name": "nonce", "type": "uint256"},
                {"name": "message", "type": "string"},
            ],
        },
        "domain": {
            "name": DOMINIO_DE_AUTH,
            "version": VERSAO_DO_DOMINIO,
            "chainId": chain_id,
        },
        "primaryType": TIPO_PRIMARIO_DE_AUTH,
        "message": {
            "address": endereco,
            "timestamp": str(timestamp),
            "nonce": nonce,
            "message": MENSAGEM_DE_AUTH,
        },
    }


def cabecalhos_l1(
    assinador: AssinadorL1,
    *,
    nonce: int = 0,
    timestamp: int | None = None,
    chain_id: int = CHAIN_ID_POLYGON,
) -> dict[str, str]:
    """Cabeçalhos do L1. Serve para DERIVAR credenciais, não para operar.

    `[VERIFICADO]` API_NOTES §3: `POLY_ADDRESS`, `POLY_SIGNATURE`,
    `POLY_TIMESTAMP`, `POLY_NONCE`.
    """
    agora = int(time.time()) if timestamp is None else timestamp
    typed_data = typed_data_de_auth(
        endereco=assinador.endereco,
        timestamp=agora,
        nonce=nonce,
        chain_id=chain_id,
    )
    return {
        "POLY_ADDRESS": assinador.endereco,
        "POLY_SIGNATURE": assinador.assinar_typed_data(typed_data),
        "POLY_TIMESTAMP": str(agora),
        "POLY_NONCE": str(nonce),
    }
