"""O livro de cada token, mantido ao vivo a partir do WS de mercado.

Fino de propósito: `OrderBook` (em `backtest/book.py`) já sabe absorver
snapshot e delta, calcular topo e medir profundidade. O nome diz "backtest"
porque foi lá que nasceu, mas a classe é agnóstica — o evento que ela recebe é
o mesmo, venha do fio ou de uma gravação. Reusá-la é o que garante que o
SHADOW meça profundidade **do mesmo jeito** que o backtest mediu os 87,8 USDC
do critério 1.5.

Duas defesas que este módulo carrega, e as duas foram pagas caro no M2:

**1. Silêncio é POR TOKEN, não por feed.** O M2.7 e o M2.10 aprenderam isso no
RTDS: o watchdog da conexão inteira não pegava o tópico mudo, porque o outro
tópico continuava chegando e a conexão parecia viva. Aqui é igual — o feed do
CLOB pode estar impecável enquanto o livro de um token específico não recebe
nada há minutos. Operar sobre esse livro é operar sobre um preço que já não
existe, e o portão `feed_parado`, que olha o feed, não veria.

**2. Delta sem snapshot é contado, não engolido.** Um `price_change` que chega
antes do primeiro `book` não tem o que atualizar. A gravação de 20 h mediu
**187.452** dessas observações. Aplicá-las a um livro vazio inventaria
profundidade; ignorá-las em silêncio esconderia que o livro está incompleto.
Elas viram contador, e o livro fica marcado como não confiável até o snapshot
chegar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pulsearb.backtest.book import OrderBook
from pulsearb.feeds.poly_ws import (
    EVENT_BOOK,
    EVENT_PRICE_CHANGE,
)
from pulsearb.obs.logging import get_logger

log = get_logger(__name__)

#: A partir de quanto tempo sem evento o livro de um token deixa de descrever
#: o presente. O `stale_after_seconds_book` do M1 é 30 s para o FEED inteiro;
#: por token o critério é mais apertado, porque um token mudo no meio de um
#: feed vivo não levanta nenhum outro alarme.
SILENCIO_DO_TOKEN_S = 10.0


@dataclass
class LivroDoToken:
    """Um livro e o que se sabe sobre a confiança nele."""

    token_id: str
    livro: OrderBook
    ultimo_evento_ns: int
    tem_snapshot: bool = False
    deltas_sem_snapshot: int = 0

    def idade_s(self, agora_ns: int) -> float:
        return max(0.0, (agora_ns - self.ultimo_evento_ns) / 1e9)

    def confiavel(self, agora_ns: int, *, silencio_s: float) -> bool:
        """Serve para decidir? Só com snapshot e sem silêncio.

        As duas condições são independentes e as duas são necessárias: um
        livro sem snapshot está incompleto mesmo recém-atualizado, e um livro
        completo parado há um minuto descreve um mercado que já mudou.
        """
        return self.tem_snapshot and self.idade_s(agora_ns) <= silencio_s


@dataclass
class LivrosAoVivo:
    """Todos os livros que interessam, atualizados por evento do WS."""

    silencio_do_token_s: float = SILENCIO_DO_TOKEN_S
    por_token: dict[str, LivroDoToken] = field(default_factory=dict)
    #: Deltas que chegaram sem snapshot, somados. Contador, não erro: é a
    #: medida de quanto do fio o bot ainda não consegue usar.
    deltas_orfaos: int = 0
    eventos_ignorados: int = 0

    def aplicar(self, evento: dict[str, Any], *, ts_ns: int) -> None:
        tipo = evento.get("event_type")
        token_id = evento.get("asset_id")
        if not isinstance(token_id, str) or not token_id:
            self.eventos_ignorados += 1
            return

        if tipo == EVENT_BOOK:
            livro = OrderBook.from_event(evento)
            if livro is None:
                self.eventos_ignorados += 1
                return
            self.por_token[token_id] = LivroDoToken(
                token_id=token_id,
                livro=livro,
                ultimo_evento_ns=ts_ns,
                tem_snapshot=True,
            )
            return

        if tipo != EVENT_PRICE_CHANGE:
            # `last_trade_price`, `tick_size_change` e afins não movem o
            # livro. Ignorar é correto; contar é o que permite dizer depois
            # que o silêncio de um token era silêncio de verdade.
            self.eventos_ignorados += 1
            return

        atual = self.por_token.get(token_id)
        if atual is None or not atual.tem_snapshot:
            # Aplicar isto a um livro vazio inventaria profundidade.
            self.deltas_orfaos += 1
            if atual is not None:
                atual.deltas_sem_snapshot += 1
            return

        atual.livro.apply_price_change(evento)
        atual.ultimo_evento_ns = ts_ns

    # ────────────────────────────────────────────────────────────── consulta
    def esquecer(self, token_id: str) -> bool:
        """Solta o livro de um token que não interessa mais.

        `por_token` não expira sozinho. Numa rodada de 24 h cada mercado que
        rotaciona deixaria o `OrderBook` inteiro para trás — milhares de
        livros mortos ocupando memória e sendo percorridos por todo `resumo`.

        Quem chama é quem sabe que a janela acabou: o processo, ao desassinar.
        """
        return self.por_token.pop(token_id, None) is not None

    def confiavel(self, token_id: str, *, agora_ns: int) -> bool:
        registro = self.por_token.get(token_id)
        return registro is not None and registro.confiavel(
            agora_ns, silencio_s=self.silencio_do_token_s
        )

    def livro(self, token_id: str, *, agora_ns: int) -> OrderBook | None:
        """O livro, ou None se ele não serve para decidir.

        Devolver None em vez de um livro suspeito é a mesma regra dos
        portões: quem não sabe não deixa passar.

        **DEVOLVE O OBJETO VIVO, não uma cópia.** Ele muda embaixo de quem o
        guardar: o próximo delta reescreve o mesmo livro. Leia o que precisa
        (topo, profundidade) na hora e siga; se for preciso segurar o estado
        de um instante, chame `.clone()` explicitamente.

        Não é descuido — é escolha. Clonar a cada consulta custaria uma cópia
        do livro por tick por token no caminho quente, para proteger um uso
        que a decisão não faz: ela lê e decide no mesmo instante.
        """
        if not self.confiavel(token_id, agora_ns=agora_ns):
            return None
        return self.por_token[token_id].livro

    def resumo(self, *, agora_ns: int) -> dict[str, Any]:
        confiaveis = sum(
            1
            for r in self.por_token.values()
            if r.confiavel(agora_ns, silencio_s=self.silencio_do_token_s)
        )
        sem_snapshot = sum(1 for r in self.por_token.values() if not r.tem_snapshot)
        mudos = sum(
            1
            for r in self.por_token.values()
            if r.tem_snapshot and r.idade_s(agora_ns) > self.silencio_do_token_s
        )
        return {
            "tokens": len(self.por_token),
            "confiaveis": confiaveis,
            "sem_snapshot": sem_snapshot,
            "mudos": mudos,
            "deltas_orfaos": self.deltas_orfaos,
            "eventos_ignorados": self.eventos_ignorados,
            "silencio_do_token_s": self.silencio_do_token_s,
            "nota": (
                "`mudos` e o numero que nenhum outro alarme daria: o feed do "
                "CLOB pode estar impecavel enquanto o livro de um token "
                "especifico nao recebe nada ha minutos, e o portao "
                "`feed_parado` olha o FEED, nao o token. E a mesma licao do "
                "M2.7/M2.10 no RTDS — topico mudo com a conexao viva. "
                "`deltas_orfaos` alto quer dizer que o fio esta entregando "
                "mudanca de preco antes do snapshot: nao e erro, e medida de "
                "quanto do fio o bot ainda nao consegue usar."
            ),
        }
