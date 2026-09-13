"""Arbitragem de soma-dos-lados: mede se Up + Down já saiu de 1,00 USDC.

    .venv/bin/python scripts/soma_dos_lados.py ~/pulsearb-gravacao \
        --json relatorios/SOMA_72H.json

## A hipótese, e por que ela merece uma medição própria

Numa janela Up/Down os dois tokens liquidam em 1,00 USDC **juntos**: exatamente
um paga 1 e o outro paga 0. Isso cria duas arbitragens que **não dependem de
prever nada** — e portanto escapam inteiras dos critérios 1.1, 1.3 e 1.4, que
reprovaram medindo qualidade de previsão:

- **cunhar e vender**: pague 1,00 USDC, receba 1 Up + 1 Down, venda os dois nos
  respectivos *bids*. Lucra se `bid(Up) + bid(Down) > 1,00` + taxas.
- **comprar e fundir**: compre os dois nos respectivos *asks*, funda o par de
  volta em 1,00 USDC. Lucra se `ask(Up) + ask(Down) < 1,00` − taxas.

O nome do projeto promete isto desde o primeiro commit e nunca foi medido.

## O que esta medição NÃO é

Não é backtest de PnL. Ela conta **oportunidades**: instantes e episódios em que
o livro gravado permitiria o par com lucro líquido. Preencher de verdade exige
atravessar o livro com tamanho, e o tamanho disponível aparece aqui como
`capacidade_usdc` — teto, não promessa.

## Por que taxa entra, e entra dobrada

O taker paga `rate * (p(1-p))^exponent` por share (API_NOTES §15.1). As duas
pernas são taker, então o custo é a SOMA das duas taxas. Como a fórmula é
simétrica em p e 1-p, e as duas pernas somam ~1, o custo é ~`2*rate*p(1-p)` —
máximo no meio do livro (3,5 c/par em p=0,50 com r=0,07, e=1) e pequeno nos
extremos. Ignorar isso acharia "arbitragem" onde só há spread.

`bruto` (sem taxa) fica no relatório ao lado de `liquido` de propósito: se o
bruto for gordo e o líquido zero, o achado é sobre a TAXA, não sobre o livro.

## Episódios, não instantes

O mesmo topo é reafirmado dezenas de vezes por segundo. Contar instantes
transformaria uma oportunidade de 3 s em centenas de "achados". `episodios`
conta transições para o estado lucrativo e mede quanto tempo ele durou — que é
o número que decide se dá para chegar lá antes de sumir.

## Frescor do outro lado

O par só é avaliado quando o livro do OUTRO token foi atualizado há menos de
`--frescor-ms`. Sem isso, um lado parado em livro velho criaria soma falsa: o
preço que se compara não existiria mais no instante da comparação.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from pulsearb.backtest.__main__ import RecordingIndex, caminho_de_leitura
from pulsearb.backtest.book import OrderBook
from pulsearb.caminhos import caminho_de_escrita
from pulsearb.engine.fees import fee_pp_por_share
from pulsearb.feeds.poly_ws import EVENT_BOOK, EVENT_PRICE_CHANGE, eventos_do_payload
from pulsearb.replay.reader import RecordingReader, ReplayRecord

#: Idade máxima do livro do outro lado para a soma valer como simultânea.
#: 2 s é FROUXO de propósito no default: o relatório precisa MOSTRAR a massa
#: que a dessincronia cria, senão o corte vira fé. Quem decide é
#: `idade_do_outro_lado_nos_lucrativos_ms`.
FRESCOR_PADRAO_MS = 2000.0

#: Latência que uma ordem real leva para chegar ao livro (1.1/1.4 do
#: VEREDITO_M2 mediram em 300 ms e 600 ms). Oportunidade que dura MENOS que
#: isto não é oportunidade: ela já terá sumido quando a ordem chegar.
LATENCIA_PADRAO_MS = 300.0

#: Quantos exemplos guardar de cada direção. Exemplo é para conferir a conta
#: na mão, não para virar amostra — por isso poucos.
MAX_EXEMPLOS = 20


def _fee(preco: float, rate: float, exponent: float) -> float | None:
    """Taxa do taker no preço, ou None se o preço não é operável.

    p em 0 ou 1 não é preço: é o livro dizendo que aquele lado sumiu. Devolver
    0,0 ali faria a arbitragem parecer de graça exatamente onde ela não existe.
    """
    if not 0.0 < preco < 1.0:
        return None
    return fee_pp_por_share(preco, rate=rate, exponent=exponent)


class _Episodio:
    """Uma janela contígua de tempo em que o par esteve lucrativo."""

    __slots__ = ("capacidade_no_melhor", "fim_ns", "inicio_ns", "melhor", "n")

    def __init__(self, ts_ns: int, lucro: float, capacidade: float) -> None:
        self.inicio_ns = ts_ns
        self.fim_ns = ts_ns
        self.melhor = lucro
        self.capacidade_no_melhor = capacidade
        self.n = 1

    def estender(self, ts_ns: int, lucro: float, capacidade: float) -> None:
        self.fim_ns = ts_ns
        self.n += 1
        if lucro > self.melhor:
            self.melhor = lucro
            self.capacidade_no_melhor = capacidade


class Direcao:
    """Acumulador de uma das duas arbitragens."""

    def __init__(self, nome: str, *, latencia_ms: float) -> None:
        self.nome = nome
        self.latencia_s = latencia_ms / 1000.0
        self.instantes_avaliados = 0
        self.instantes_bruto_positivo = 0
        self.instantes_liquido_positivo = 0
        self.slugs_com_bruto: set[str] = set()
        self.slugs_com_liquido: set[str] = set()
        self.episodios: list[dict[str, Any]] = []
        self.exemplos: list[dict[str, Any]] = []
        self.histograma: Counter[str] = Counter()
        # A idade do livro do OUTRO lado NOS INSTANTES LUCRATIVOS. É o
        # diagnóstico que separa arbitragem de dessincronia: se o lucro só
        # aparece quando o outro lado está velho, o gap nunca existiu
        # simultaneamente — é o preço de antes comparado com o de agora.
        self.idade_do_outro: Counter[str] = Counter()
        self._abertos: dict[str, _Episodio] = {}

    # -------------------------------------------------------------- registro
    def observar(
        self,
        *,
        slug: str,
        ts_ns: int,
        bruto: float,
        liquido: float,
        capacidade: float,
        idade_do_outro_ms: float,
        detalhe: dict[str, Any],
    ) -> None:
        self.instantes_avaliados += 1
        self.histograma[_faixa(liquido)] += 1
        if bruto > 0:
            self.instantes_bruto_positivo += 1
            self.slugs_com_bruto.add(slug)
        if liquido > 0:
            self.instantes_liquido_positivo += 1
            self.slugs_com_liquido.add(slug)
            self.idade_do_outro[_faixa_idade(idade_do_outro_ms)] += 1
            aberto = self._abertos.get(slug)
            if aberto is None:
                self._abertos[slug] = _Episodio(ts_ns, liquido, capacidade)
            else:
                aberto.estender(ts_ns, liquido, capacidade)
            if len(self.exemplos) < MAX_EXEMPLOS:
                self.exemplos.append(detalhe)
        else:
            self._fechar(slug)

    def _fechar(self, slug: str) -> None:
        aberto = self._abertos.pop(slug, None)
        if aberto is None:
            return
        self.episodios.append(
            {
                "slug": slug,
                "inicio_ns": aberto.inicio_ns,
                "duracao_s": round((aberto.fim_ns - aberto.inicio_ns) / 1e9, 4),
                "instantes": aberto.n,
                "melhor_lucro_usdc_por_par": round(aberto.melhor, 6),
                "capacidade_usdc_no_melhor": round(aberto.capacidade_no_melhor, 2),
            }
        )

    def finalizar(self) -> None:
        for slug in list(self._abertos):
            self._fechar(slug)

    # --------------------------------------------------------------- saída
    def resumo(self) -> dict[str, Any]:
        duracoes = sorted(e["duracao_s"] for e in self.episodios)
        sobreviventes = [
            e for e in self.episodios if e["duracao_s"] >= self.latencia_s
        ]
        melhores = sorted(
            self.episodios,
            key=lambda e: e["melhor_lucro_usdc_por_par"],
            reverse=True,
        )[:MAX_EXEMPLOS]
        return {
            "instantes_avaliados": self.instantes_avaliados,
            "instantes_com_lucro_bruto": self.instantes_bruto_positivo,
            "instantes_com_lucro_liquido": self.instantes_liquido_positivo,
            "fracao_dos_instantes_com_lucro_liquido": (
                round(self.instantes_liquido_positivo / self.instantes_avaliados, 8)
                if self.instantes_avaliados
                else 0.0
            ),
            "janelas_com_lucro_bruto": len(self.slugs_com_bruto),
            "janelas_com_lucro_liquido": len(self.slugs_com_liquido),
            "episodios": len(self.episodios),
            # O NÚMERO QUE DECIDE. Episódio que dura menos que a latência já
            # sumiu quando a ordem chega — conta como observação, nunca como
            # oportunidade. Zero aqui com `episodios` alto é a assinatura de
            # livro dessincronizado, não de arbitragem.
            "episodios_que_sobrevivem_a_latencia": len(sobreviventes),
            "latencia_exigida_s": self.latencia_s,
            "melhores_sobreviventes": sorted(
                sobreviventes,
                key=lambda e: e["melhor_lucro_usdc_por_par"],
                reverse=True,
            )[:MAX_EXEMPLOS],
            "duracao_dos_episodios_s": {
                "p50": duracoes[len(duracoes) // 2] if duracoes else None,
                "p90": duracoes[int(len(duracoes) * 0.9)] if duracoes else None,
                "max": duracoes[-1] if duracoes else None,
                "total": round(sum(duracoes), 3) if duracoes else 0.0,
            },
            "melhores_episodios": melhores,
            "exemplos": self.exemplos,
            "histograma_do_lucro_liquido_usdc_por_par": dict(
                sorted(self.histograma.items())
            ),
            "idade_do_outro_lado_nos_lucrativos_ms": dict(
                sorted(self.idade_do_outro.items())
            ),
        }


def _faixa_idade(ms: float) -> str:
    """Bucket da idade do livro do outro lado, em ms.

    O primeiro bucket é o único em que os dois preços descrevem o MESMO
    instante. Massa fora dele é dessincronia, não oportunidade."""
    if ms <= 1.0:
        return "a: <= 1ms"
    if ms <= 10.0:
        return "b: 1..10ms"
    if ms <= 50.0:
        return "c: 10..50ms"
    if ms <= 300.0:
        return "d: 50..300ms"
    return "e: > 300ms"


def _faixa(lucro: float) -> str:
    """Bucket do lucro líquido por par, em USDC. Assimétrico de propósito:
    o lado negativo só precisa de ordem de grandeza, o positivo precisa de
    resolução fina — é lá que a decisão mora."""
    if lucro <= -0.10:
        return "a: <= -0.10"
    if lucro <= -0.03:
        return "b: -0.10..-0.03"
    if lucro <= -0.01:
        return "c: -0.03..-0.01"
    if lucro <= 0.0:
        return "d: -0.01..0"
    if lucro <= 0.001:
        return "e: 0..0.001"
    if lucro <= 0.005:
        return "f: 0.001..0.005"
    if lucro <= 0.02:
        return "g: 0.005..0.02"
    return "h: > 0.02"


class IndiceDaSoma(RecordingIndex):
    """`RecordingIndex` que, na passada 2, mede a soma em vez de guardar livro.

    NÃO chama `_timeline`: a arbitragem é avaliada no instante e descartada,
    então a passada 2 roda em memória constante. É o que permite varrer 72 h
    numa máquina de 8 GB, onde o backtest de fills precisou de fatias por dia.
    """

    def __init__(
        self,
        reader: RecordingReader,
        *,
        frescor_ms: float,
        latencia_ms: float = LATENCIA_PADRAO_MS,
    ) -> None:
        super().__init__(reader)
        self.frescor_ns = int(frescor_ms * 1e6)
        self.par: dict[str, str] = {}
        self.slug_do_token: dict[str, str] = {}
        self.taxa_do_token: dict[str, tuple[float, float]] = {}
        self.ultimo_ns: dict[str, int] = {}
        self.vender = Direcao("cunhar_e_vender", latencia_ms=latencia_ms)
        self.comprar = Direcao("comprar_e_fundir", latencia_ms=latencia_ms)
        self.pares_sem_o_outro_lado = 0
        self.instantes_com_lado_vazio = 0
        self.instantes_com_par_velho = 0

    def _marcar_tokens_de_interesse(self) -> None:
        super()._marcar_tokens_de_interesse()
        for slug, meta in self.janelas_por_slug.items():
            tokens = meta.get("token_id_by_outcome") or {}
            up, down = tokens.get("Up"), tokens.get("Down")
            if not isinstance(up, str) or not isinstance(down, str):
                continue
            taxa = (
                float(meta.get("fee_rate") or 0.0),
                float(meta.get("fee_exponent") or 1.0),
            )
            self.par[up], self.par[down] = down, up
            self.slug_do_token[up] = self.slug_do_token[down] = slug
            self.taxa_do_token[up] = self.taxa_do_token[down] = taxa

    def _on_poly_book(self, record: ReplayRecord) -> None:
        for evento in eventos_do_payload(record.payload):
            tipo = evento.get("event_type")
            if tipo not in (EVENT_BOOK, EVENT_PRICE_CHANGE):
                continue
            asset_id = evento.get("asset_id")
            if not isinstance(asset_id, str):
                continue
            intervalo = self.janelas_de_interesse.get(asset_id)
            if intervalo is None or not (
                intervalo[0] <= record.ts_wall_ns <= intervalo[1]
            ):
                continue
            if tipo == EVENT_BOOK:
                book = OrderBook.from_event(evento)
                if book is None:
                    continue
                self.book_atual[asset_id] = book
            else:
                book = self.book_atual.get(asset_id)
                if book is None:
                    continue
                book.apply_price_change(evento)
            self.ultimo_ns[asset_id] = record.ts_wall_ns
            self._avaliar(asset_id, record.ts_wall_ns)

    # ------------------------------------------------------------- avaliação
    def _avaliar(self, token: str, ts_ns: int) -> None:
        outro = self.par.get(token)
        if outro is None:
            self.pares_sem_o_outro_lado += 1
            return
        aqui = self.book_atual.get(token)
        la = self.book_atual.get(outro)
        if aqui is None or la is None:
            self.pares_sem_o_outro_lado += 1
            return
        idade_ns = ts_ns - self.ultimo_ns.get(outro, 0)
        if idade_ns > self.frescor_ns:
            self.instantes_com_par_velho += 1
            return
        slug = self.slug_do_token.get(token, "?")
        rate, exponent = self.taxa_do_token.get(token, (0.0, 1.0))
        idade_ms = idade_ns / 1e6

        self._vender(slug, ts_ns, aqui, la, rate, exponent, idade_ms)
        self._comprar(slug, ts_ns, aqui, la, rate, exponent, idade_ms)

    def _vender(
        self,
        slug: str,
        ts_ns: int,
        a: OrderBook,
        b: OrderBook,
        rate: float,
        exponent: float,
        idade_do_outro_ms: float,
    ) -> None:
        """Cunhar por 1,00 e vender os dois lados nos bids."""
        if not a.bids or not b.bids:
            self.instantes_com_lado_vazio += 1
            return
        pa, sa = a.bids[0]
        pb, sb = b.bids[0]
        fa, fb = _fee(pa, rate, exponent), _fee(pb, rate, exponent)
        if fa is None or fb is None:
            self.instantes_com_lado_vazio += 1
            return
        bruto = (pa + pb) - 1.0
        liquido = bruto - fa - fb
        pares = min(sa, sb)
        self.vender.observar(
            slug=slug,
            ts_ns=ts_ns,
            bruto=bruto,
            liquido=liquido,
            # Cunhar custa 1,00 USDC por par: a capacidade em dinheiro é o
            # número de pares, não o valor dos bids.
            capacidade=pares,
            idade_do_outro_ms=idade_do_outro_ms,
            detalhe={
                "slug": slug,
                "ts_ns": ts_ns,
                "bid_a": pa,
                "bid_b": pb,
                "soma": round(pa + pb, 6),
                "taxa_total": round(fa + fb, 6),
                "lucro_liquido_usdc_por_par": round(liquido, 6),
                "pares_disponiveis": pares,
                "idade_do_outro_lado_ms": round(idade_do_outro_ms, 3),
            },
        )

    def _comprar(
        self,
        slug: str,
        ts_ns: int,
        a: OrderBook,
        b: OrderBook,
        rate: float,
        exponent: float,
        idade_do_outro_ms: float,
    ) -> None:
        """Comprar os dois lados nos asks e fundir o par em 1,00."""
        if not a.asks or not b.asks:
            self.instantes_com_lado_vazio += 1
            return
        pa, sa = a.asks[0]
        pb, sb = b.asks[0]
        fa, fb = _fee(pa, rate, exponent), _fee(pb, rate, exponent)
        if fa is None or fb is None:
            self.instantes_com_lado_vazio += 1
            return
        bruto = 1.0 - (pa + pb)
        liquido = bruto - fa - fb
        pares = min(sa, sb)
        self.comprar.observar(
            slug=slug,
            ts_ns=ts_ns,
            bruto=bruto,
            liquido=liquido,
            capacidade=pares * (pa + pb),
            idade_do_outro_ms=idade_do_outro_ms,
            detalhe={
                "slug": slug,
                "ts_ns": ts_ns,
                "ask_a": pa,
                "ask_b": pb,
                "soma": round(pa + pb, 6),
                "taxa_total": round(fa + fb, 6),
                "lucro_liquido_usdc_por_par": round(liquido, 6),
                "pares_disponiveis": pares,
                "idade_do_outro_lado_ms": round(idade_do_outro_ms, 3),
            },
        )

    def relatorio(self) -> dict[str, Any]:
        self.vender.finalizar()
        self.comprar.finalizar()
        return {
            "gravacao": {
                "arquivos": len(self.reader.files),
                "registros": self.reader.total,
                "snapshots_de_descoberta": self.n_snapshots,
                "janelas_conhecidas": len(self.janelas_por_slug),
                "tokens_pareados": len(self.par),
            },
            "parametros": {
                "frescor_ms": self.frescor_ns / 1e6,
                "latencia_ms": self.vender.latencia_s * 1000.0,
                "nota_frescor": (
                    "O par só é avaliado quando o livro do OUTRO token foi "
                    "atualizado há menos disto. Sem o corte, um lado parado "
                    "produziria soma que não existiu em instante nenhum."
                ),
            },
            "descartes": {
                "sem_o_outro_lado": self.pares_sem_o_outro_lado,
                "par_velho": self.instantes_com_par_velho,
                "lado_vazio_ou_preco_degenerado": self.instantes_com_lado_vazio,
                "nota": (
                    "`sem_o_outro_lado` é o token cujo par nunca apareceu no "
                    "fio — não é defeito da medição, é ausência de livro. "
                    "`lado_vazio_ou_preco_degenerado` inclui preço 0 ou 1, "
                    "que não é preço: é o livro dizendo que o lado sumiu."
                ),
            },
            "cunhar_e_vender": self.vender.resumo(),
            "comprar_e_fundir": self.comprar.resumo(),
            "leitura": (
                "`instantes_com_lucro_bruto` alto com `instantes_com_lucro_"
                "liquido` zero quer dizer que a TAXA come a arbitragem — o "
                "livro tem o gap, o taker não consegue pegá-lo. "
                "`episodios` é o número que decide: cada um é uma "
                "oportunidade distinta, e `duracao_dos_episodios_s` diz se "
                "daria tempo de chegar lá. Instante não é oportunidade: o "
                "mesmo topo é reafirmado dezenas de vezes por segundo."
            ),
        }


def _hora_utc(bruto: str | None) -> datetime | None:
    if not bruto:
        return None
    return datetime.fromisoformat(bruto).replace(tzinfo=UTC)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="soma_dos_lados",
        description="Mede arbitragem de soma-dos-lados sobre a gravação.",
    )
    parser.add_argument("recordings")
    parser.add_argument("--json", default=None)
    parser.add_argument("--desde", default=None)
    parser.add_argument("--ate", default=None)
    parser.add_argument("--frescor-ms", type=float, default=FRESCOR_PADRAO_MS)
    parser.add_argument("--latencia-ms", type=float, default=LATENCIA_PADRAO_MS)
    args = parser.parse_args(argv)

    try:
        caminho = caminho_de_leitura(args.recordings)
        destino = caminho_de_escrita(args.json) if args.json else None
        desde, ate = _hora_utc(args.desde), _hora_utc(args.ate)
    except ValueError as erro:
        print(str(erro), file=sys.stderr)
        return 2

    reader = RecordingReader(caminho, desde=desde, ate=ate)
    index = IndiceDaSoma(
        reader, frescor_ms=args.frescor_ms, latencia_ms=args.latencia_ms
    )
    index.build()

    if not index.n_snapshots:
        print(
            "nenhum snapshot de descoberta — sem metadados não há par Up/Down",
            file=sys.stderr,
        )
        return 1

    relatorio = index.relatorio()
    texto = json.dumps(relatorio, indent=2, ensure_ascii=False)
    if destino is not None:
        destino.write_text(texto, encoding="utf-8")
        print(f"\nrelatório gravado em {destino}")
    else:
        print(texto)

    v = relatorio["cunhar_e_vender"]
    c = relatorio["comprar_e_fundir"]
    for nome, r in (("cunhar_e_vender ", v), ("comprar_e_fundir", c)):
        print(
            f"\n{nome}: {r['episodios']} episódio(s) brutos, "
            f"{r['episodios_que_sobrevivem_a_latencia']} sobrevive(m) a "
            f"{r['latencia_exigida_s']}s de latência"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
