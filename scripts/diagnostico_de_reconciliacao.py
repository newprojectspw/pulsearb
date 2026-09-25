"""Diagnóstico offline da reconciliação do livro CLOB.

    python scripts/diagnostico_de_reconciliacao.py data/recordings/pulsearb-20260924-1900.jsonl.gz

Existe porque a causa raiz das divergências PERSISTENTES e dos tokens
COMPROMETIDOS não se lê do relatório agregado — ela é por token, e depende de
distinguir quatro coisas que o número total confunde:

1. **corrida de um tick** — o `best_bid_ask` e o `price_change` que a corrige
   ainda não se cruzaram. NORMAL (M2.5), some com o alinhamento por carimbo.
2. **snapshot com profundidade truncada** — o servidor afirma um topo num
   nível que nunca nos foi enviado (estava abaixo da profundidade do snapshot).
   A reconstrução fica errada e NÃO se conserta com delta nenhum.
3. **delta perdido** — a fila transbordou (incidente) e um delta sumiu. Aqui
   NÃO aconteceu (`incidentes_de_fila = 0`), mas o diagnóstico o distingue.
4. **resync não reproduzido no backtest** — o recorder, ao vivo, marcou perda
   e reassinou (o `book` de recuperação está gravado como `resync_book` +
   `book`). O backtest, porém, NÃO chama `marcar_perda`, então a sua
   reconstrução nunca é re-ancorada e a divergência que o recorder já tinha
   consertado ao vivo PERSISTE no backtest — um descompasso de *mesmo caminho*.

A ferramenta roda o MESMO `MonitorDeIntegridade` do backtest sobre a gravação,
em dois modos — sem e com a reprodução dos resyncs (`--replay-resync`) — e põe
as métricas lado a lado. Se os persistentes/comprometidos CAEM ao reproduzir
os resyncs, a causa era o item 4; se NÃO caem, é truncagem/perda (itens 2/3),
e o relatório por token diz em qual nível o servidor apontava.

Não altera nada da política de integridade. Só MEDE.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pulsearb.analysis.integrity import MAGNITUDE_CRITICA, MonitorDeIntegridade
from pulsearb.caminhos import caminho_de_escrita
from pulsearb.feeds.poly_ws import eventos_do_payload
from pulsearb.recorder.writer import FONTE_RESYNC


def _motivo_comprometido(monitor: MonitorDeIntegridade, token: str) -> dict[str, Any]:
    """Por que ESTE token é `baixa` — a pergunta que o total não responde."""
    estado = monitor.estados[token]
    if not estado.teve_snapshot:
        motivo = "nunca_teve_snapshot"
    elif estado.magnitude_persistente_max > MAGNITUDE_CRITICA:
        motivo = "magnitude_persistente_critica"
    else:
        motivo = "fracao_de_tempo_ruim"
    return {
        "token": token,
        "motivo": motivo,
        "teve_snapshot": estado.teve_snapshot,
        "fracao_ruim": round(estado.fracao_ruim, 6),
        "ms_divergentes": round(estado.ms_divergentes, 1),
        "ms_sem_livro": round(estado.ms_sem_livro, 1),
        "ms_observados": round(estado.ms_observados, 1),
        "magnitude_persistente_max": round(estado.magnitude_persistente_max, 6),
        "divergencias_persistentes": estado.persistentes,
    }


def relatorio_de_diagnostico(monitor: MonitorDeIntegridade) -> dict[str, Any]:
    """As métricas que decidem, mais o porquê de cada token comprometido."""
    resumo = monitor.resumo()
    comprometidos = [
        _motivo_comprometido(monitor, token)
        for token in monitor.estados
        if monitor.token_corrompido(token)
    ]
    comprometidos.sort(key=lambda c: c["fracao_ruim"], reverse=True)
    persistentes = sum(e.persistentes for e in monitor.estados.values())
    return {
        "metricas": {
            "divergencias_alinhadas": resumo["divergencias"],
            "divergencias_transientes_ignoradas": (
                monitor.divergencias_transientes_ignoradas
            ),
            "resyncs_por_material": monitor.resyncs_por_material,
            "resyncs_por_persistencia": monitor.resyncs_por_persistencia,
            "divergencias_persistentes": persistentes,
            "tokens_comprometidos": len(comprometidos),
            "tokens_aguardando_resync": len(monitor.aguardando_resync),
            "snapshots_fora_de_ordem": resumo["alinhamento"][
                "snapshots_com_carimbo_fora_de_ordem"
            ],
            "observacoes_sem_snapshot": monitor.observacoes_sem_snapshot,
            "formas_de_price_change": resumo["formas_de_price_change"],
            "formas_de_book": resumo["snapshots_de_livro"]["formas"],
            "niveis_por_lado": resumo["snapshots_de_livro"]["niveis_por_lado"],
        },
        "motivos_dos_comprometidos": _contagem_de_motivos(comprometidos),
        "tokens_comprometidos": comprometidos[:50],
        "amostras_de_divergencia": resumo["amostras"][:20],
    }


def _contagem_de_motivos(comprometidos: list[dict[str, Any]]) -> dict[str, int]:
    contagem: dict[str, int] = {}
    for c in comprometidos:
        contagem[c["motivo"]] = contagem.get(c["motivo"], 0) + 1
    return contagem


def diagnosticar(registros: Iterable[Any], *, replay_resync: bool) -> dict[str, Any]:
    """Roda o monitor sobre os registros. Núcleo PURO — o teste passa objetos
    com `.fonte`, `.payload`, `.ts_wall_ns`; o `main` passa `RecordingReader`.

    Com `replay_resync`, cada registro `resync_book` vira `marcar_perda` dos
    tokens dele — reproduzindo o que o recorder fez AO VIVO, para o diagnóstico
    medir se o descompasso de reprodução (item 4) explica os persistentes."""
    monitor = MonitorDeIntegridade()
    for rec in registros:
        if rec.fonte == FONTE_RESYNC:
            if replay_resync and isinstance(rec.payload, dict):
                for token in rec.payload.get("tokens", []):
                    if isinstance(token, str):
                        monitor.marcar_perda(token)
            continue
        if rec.fonte != "poly_ws":
            continue
        for evento in eventos_do_payload(rec.payload):
            monitor.observar(evento, rec.ts_wall_ns)
    monitor.finalizar()
    return relatorio_de_diagnostico(monitor)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gravacao", help="arquivo .jsonl.gz ou diretório")
    parser.add_argument(
        "--json", default=None, help="grava o relatório comparativo neste caminho"
    )
    args = parser.parse_args(argv)

    # Import tardio: o script roda no Mac/VPS; o teste do núcleo não precisa
    # do reader nem de um arquivo.
    from pulsearb.replay.reader import RecordingReader

    caminho = Path(args.gravacao)
    sem = diagnosticar(RecordingReader(caminho), replay_resync=False)
    com = diagnosticar(RecordingReader(caminho), replay_resync=True)
    comparativo = {
        "arquivo": str(caminho),
        "sem_replay_de_resync": sem["metricas"],
        "com_replay_de_resync": com["metricas"],
        "leitura": _leitura(sem["metricas"], com["metricas"]),
        "detalhe_sem_replay": {
            "motivos_dos_comprometidos": sem["motivos_dos_comprometidos"],
            "tokens_comprometidos": sem["tokens_comprometidos"],
            "amostras_de_divergencia": sem["amostras_de_divergencia"],
        },
    }
    saida = json.dumps(comparativo, indent=2, ensure_ascii=False)
    print(saida)
    if args.json:
        # `caminho_de_escrita` contém o destino à raiz permitida (S2083): um
        # `--json` não sanitizado é caminho de saída não confiável. Ver
        # `caminhos.py`.
        caminho_de_escrita(args.json).write_text(saida, encoding="utf-8")
    return 0


def _leitura(sem: dict[str, Any], com: dict[str, Any]) -> str:
    """O veredito que o comparativo permite, em uma frase."""
    caiu_persistente = com["divergencias_persistentes"] < sem["divergencias_persistentes"]
    caiu_comprometido = com["tokens_comprometidos"] < sem["tokens_comprometidos"]
    if caiu_persistente or caiu_comprometido:
        return (
            "Reproduzir os resyncs REDUZ persistentes/comprometidos: a causa é "
            "o backtest NÃO reproduzir os resyncs do recorder (item 4) — a "
            "reconstrução do backtest não é re-ancorada como a do recorder ao "
            "vivo. Ver `motivos_dos_comprometidos` para o resíduo."
        )
    return (
        "Reproduzir os resyncs NÃO reduz: os persistentes são truncagem de "
        "profundidade ou delta perdido (itens 2/3). Cheque "
        "`tokens_comprometidos` (fracao_ruim, magnitude) e `niveis_por_lado` — "
        "topo afirmado abaixo do nível mais raso do snapshot = truncagem."
    )


if __name__ == "__main__":
    raise SystemExit(main())
