"""A âncora verificada (τ=0, stream twap_sixty no fecho) explica CADA duração?

    python scripts/janela_do_twap.py ~/pulsearb-gravacao \\
        --desde 2026-09-09T02:19Z --ate 2026-09-12T02:19Z \\
        --json relatorios/JANELA_TWAP_M2_72H.json

## Por que existe (auditoria 2026-09-17 §2.2), e o que a v1 mediu de errado

`engine/twap.py` usa 60 s para TODAS as durações; o API_NOTES §7 diz "5m usa
30 s" e o §12.3, sem data, "60 s para todas". A v1 deste script comparou
janelas de 30 e 60 s calculando a MÉDIA das amostras do stream antes do
fecho — e o M2.6 já tinha provado que essa definição de final perde
(`final_media_60s` 0,9648 contra `final_stream_no_fechamento` 1,0; API_NOTES
§13.8). Na M2_72H a v1 deu 55 e 102 "erros" para 5m: ruído da definição
errada, não evidência sobre a janela. Um script que mede pela definição que
sabidamente perde não responde a pergunta nenhuma.

## O que esta versão mede

O MESMO caminho da varredura τ do backtest (`analysis/anchor_sweep.varrer` +
`veredito_da_ancora`): âncora = valor do stream `twap_sixty` na abertura,
final = valor do stream no fecho, inteiros na escala 1e18, eixo = carimbo do
servidor. Só que **por duração** (300, 900, 14400 s), em vez de tudo junto.

O que responde: se τ=0 com o stream de 60 s explica as resoluções das janelas
de 5m tão bem quanto as de 15m e 4h, então o que liquida as de 5m é o TWAP de
60 s, e a nota do §12.3 pode ganhar data. Se as de 5m discordarem mais, a
janela delas é outra — e a prova final exige gravar `crypto_prices_twap_thirty`
(o recorder só assina o de 60 s; `feeds/rtds.TOPICOS_ASSINADOS`).

| veredito | quando |
|---|---|
| `CONFIRMADA` | τ=0 explica ≥ 98 % das elegíveis (limiar do backtest) |
| `DESMENTIDA` | abaixo do limiar |
| `SEM_AMOSTRA` | menos de 20 elegíveis, ou todas indeterminadas |
| `SEM_DADO` | nenhuma janela resolvida da duração |
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from pulsearb.analysis.anchor_sweep import (
    JanelaResolvida,
    varrer,
    veredito_da_ancora,
)
from pulsearb.backtest.__main__ import Progresso, RecordingIndex, caminho_de_leitura
from pulsearb.caminhos import caminho_de_escrita
from pulsearb.replay.reader import RecordingReader

VEREDITOS: dict[str, str] = {
    "CONFIRMADA": "τ=0 com o stream twap_sixty explica as resoluções desta duração",
    "DESMENTIDA": "τ=0 com o stream twap_sixty NÃO explica esta duração — a janela é outra",
    "SEM_AMOSTRA": "poucas janelas elegíveis, ou todas indeterminadas — sem evidência",
    "SEM_DADO": "nenhuma janela resolvida desta duração",
}


def _rotulo(veredito: dict[str, Any]) -> str:
    confirmada = veredito.get("confirmada")
    if confirmada is True:
        return "CONFIRMADA"
    if confirmada is False:
        return "DESMENTIDA"
    return "SEM_AMOSTRA"


def comparar(
    por_duracao: dict[int, list[JanelaResolvida]],
    streams_e18: dict[str, list[tuple[int, int]]],
) -> dict[str, Any]:
    """A varredura τ do backtest, por duração. Pura."""
    saida: dict[str, Any] = {}
    for duracao_s in sorted(por_duracao):
        janelas = por_duracao[duracao_s]
        if not janelas:
            rotulo, veredito, varredura = "SEM_DADO", {}, {}
        else:
            varredura = varrer(janelas, streams_e18)
            veredito = veredito_da_ancora(varredura)
            rotulo = _rotulo(veredito)
        fino = varredura.get("final_stream_no_fechamento") or {}
        saida[str(duracao_s)] = {
            "duracao_s": duracao_s,
            "veredito": rotulo,
            "o_que_significa": VEREDITOS[rotulo],
            "janelas_recebidas": len(janelas),
            "janelas_elegiveis": veredito.get("janelas_elegiveis"),
            "consistencia_tau0": veredito.get("consistencia_do_tau_verificado"),
            "janelas_discordantes": veredito.get("janelas_discordantes"),
            "janelas_indeterminadas": veredito.get("janelas_indeterminadas"),
            "regiao_viavel_100pct": veredito.get("regiao_viavel_100pct"),
            "melhores_tau": (fino.get("melhores_tau") or [])[:3],
            "discordantes_em_tau0": varredura.get("discordantes_em_tau_verificado") or [],
            "texto_do_veredito": veredito.get("veredito"),
        }
    return saida


def _hora_utc(bruto: str | None) -> datetime | None:
    """ISO 8601 → UTC. Offset explícito é CONVERTIDO, não relabelado."""
    if not bruto:
        return None
    lido = datetime.fromisoformat(bruto.strip().replace("Z", "+00:00"))
    return (lido if lido.tzinfo else lido.replace(tzinfo=UTC)).astimezone(UTC)


def _imprimir(rel: dict[str, Any]) -> None:
    for bloco in rel.values():
        print(
            f"\nduração {bloco['duracao_s']:>6} s: {bloco['janelas_recebidas']} recebidas, "
            f"{bloco['janelas_elegiveis']} elegíveis → {bloco['veredito']} "
            f"({bloco['o_que_significa']})"
        )
        print(
            f"  consistência em τ=0: {bloco['consistencia_tau0']}  "
            f"discordantes: {bloco['janelas_discordantes']}  "
            f"indeterminadas: {bloco['janelas_indeterminadas']}  "
            f"região 100 %: {bloco['regiao_viavel_100pct']}"
        )
        for m in bloco["melhores_tau"]:
            print(f"    τ={m['tau_s']:>4} s  {m['consistencia']}  ({m['avaliadas']} avaliadas)")
        if bloco["texto_do_veredito"]:
            print(f"  {bloco['texto_do_veredito']}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="janela_do_twap")
    p.add_argument("recordings", help="diretório (ou arquivo) da gravação")
    p.add_argument("--desde", default=None)
    p.add_argument("--ate", default=None)
    p.add_argument("--json", default=None)
    args = p.parse_args(argv)
    try:
        caminho = caminho_de_leitura(args.recordings)
        destino = caminho_de_escrita(args.json) if args.json else None
        desde, ate = _hora_utc(args.desde), _hora_utc(args.ate)
    except ValueError as erro:
        print(str(erro), file=sys.stderr)
        return 2

    reader = RecordingReader(caminho, desde=desde, ate=ate)
    # Só o stream e as resoluções interessam: livro retido no mínimo.
    index = RecordingIndex(reader, limite_por_token=1, niveis_retidos=1, progresso=Progresso())
    index.build()

    por_duracao: dict[int, list[JanelaResolvida]] = defaultdict(list)
    for j in index.janelas():
        if j.jogo != "twap" or j.resolveu_up is None:
            continue
        por_duracao[j.duracao_s].append(
            JanelaResolvida(
                slug=j.slug,
                asset=j.asset,
                abertura_ms=j.open_ts_ns // 1_000_000,
                fechamento_ms=j.close_ts_ns // 1_000_000,
                resolveu_up=bool(j.resolveu_up),
            )
        )
    if not por_duracao:
        print("nenhuma janela TWAP resolvida na gravação — nada a comparar", file=sys.stderr)
        return 1

    rel = comparar(por_duracao, dict(index.streams_e18))
    if destino is not None:
        destino.write_text(json.dumps(rel, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"relatório gravado em {destino}")
    _imprimir(rel)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
