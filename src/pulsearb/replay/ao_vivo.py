"""Replay de gravação para CicloAoVivo com relógio da própria gravação.

O problema central: ciclo.feeds_saudaveis(agora_ns) e ciclo.passo() usam
agora_ns para calcular a idade dos preços. Num replay acelerado, se passarmos
time.time_ns(), os preços gravados ontem parecerão ter chegado 24 h atrás —
feeds_saudaveis() retornaria False para todo tick, e o ciclo nunca decidiria.

A correção: agora_ns e agora_epoch vêm de record.ts_wall_ns — o relógio do
gravador, que é o mesmo relógio que o backtest enxerga quando lê a série.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pulsearb.feeds.rtds import TOPIC_TWAP_60, e18_do_evento, parse_rtds_event
from pulsearb.live.ciclo import FONTE_RTDS, CicloAoVivo
from pulsearb.markets.discovery import DiscoveredMarket
from pulsearb.recorder.writer import FONTE_DISCOVERY
from pulsearb.replay.player import ReplayPlayer
from pulsearb.replay.reader import RecordingReader


def _mercado_do_dict(janela: dict) -> DiscoveredMarket | None:
    """Reconstrói DiscoveredMarket do payload de um discovery_snapshot."""
    try:
        end_date_iso = janela.get("end_date_iso")
        return DiscoveredMarket(
            slug=janela["slug"],
            condition_id=janela["condition_id"],
            asset=janela["asset"],
            resolution=janela["resolution"],
            token_id_by_outcome=janela["token_id_by_outcome"],
            tick_size=float(janela["tick_size"]),
            min_order_size=float(janela["min_order_size"]),
            fee_rate=float(janela["fee_rate"]),
            fee_exponent=float(janela["fee_exponent"]),
            fee_taker_only=bool(janela.get("fee_taker_only", True)),
            fee_rebate_rate=janela.get("fee_rebate_rate"),
            accepting_orders=bool(janela.get("accepting_orders", True)),
            end_date_iso=end_date_iso,
            operable=bool(janela.get("operable", False)),
            gate_failures=list(janela.get("gate_failures", [])),
            raw_gamma={"endDate": end_date_iso} if end_date_iso else {},
        )
    except (KeyError, TypeError, ValueError):
        return None


@dataclass
class ResumoDoReplay:
    n_eventos: int = 0
    n_meta: int = 0
    n_discovery: int = 0
    n_passo: int = 0
    n_passo_erros: int = 0
    span_s: float = 0.0
    # Série (ts_wall_ns, valor_e18) por ativo, capturada diretamente dos
    # registros antes de passar ao ciclo — é o "caminho direto" para comparação.
    series_e18: dict[str, list[tuple[int, int]]] = field(default_factory=dict)
    # Extremos de ts_wall_ns para validar o invariante do relógio da gravação.
    ts_inicio_ns: int = 0
    ts_fim_ns: int = 0


class ReplayCiclo:
    """Alimenta um CicloAoVivo com uma gravação usando o relógio da gravação.

    Uso típico::

        ciclo = montar_ciclo(settings, caminho_do_diario=...)
        resumo = ReplayCiclo([arquivo], ciclo).executar()

    Passe ``ate_ns`` (ts_wall_ns inteiro) para parar depois dessa marca — útil
    em testes para não processar um arquivo de 400 MB inteiro.
    """

    def __init__(
        self,
        paths,
        ciclo: CicloAoVivo,
        *,
        ate_ns: int | None = None,
    ) -> None:
        self._paths = paths
        self._ate_ns = ate_ns
        self.ciclo = ciclo

    def executar(self) -> ResumoDoReplay:
        resumo = ResumoDoReplay()

        for record in RecordingReader(self._paths).iter_records():
            if self._ate_ns is not None and record.ts_wall_ns > self._ate_ns:
                break
            agora_ns: int = record.ts_wall_ns
            agora_epoch: float = agora_ns / 1e9

            if resumo.ts_inicio_ns == 0:
                resumo.ts_inicio_ns = agora_ns
            resumo.ts_fim_ns = agora_ns

            if record.is_meta:
                resumo.n_meta += 1
                if record.fonte == FONTE_DISCOVERY:
                    mercados = [
                        m
                        for j in record.payload.get("janelas", [])
                        if (m := _mercado_do_dict(j)) is not None
                    ]
                    self.ciclo.on_descoberta(mercados, agora_epoch=agora_epoch)
                    resumo.n_discovery += 1
                continue

            resumo.n_eventos += 1
            event = ReplayPlayer._to_feed_event(record)

            # Captura a série e18 antes de enviar ao ciclo — é o mesmo dado
            # que o backtest vê, e serve como referência para o "mesmo caminho".
            if event.source == FONTE_RTDS:
                tick = parse_rtds_event(
                    event.parsed, event.ts_mono_ns, event.ts_wall_ns
                )
                if (
                    tick is not None
                    and tick.topic == TOPIC_TWAP_60
                    and tick.src_timestamp_ms > 0
                ):
                    valor = e18_do_evento(event.parsed)
                    if valor is not None:
                        resumo.series_e18.setdefault(tick.asset, []).append(
                            (record.ts_wall_ns, valor)
                        )

            self.ciclo.on_feed_event(event)

            # agora_ns vem do relógio DA GRAVAÇÃO — invariante do mesmo caminho.
            try:
                self.ciclo.passo(agora_epoch=agora_epoch, agora_ns=agora_ns)
                resumo.n_passo += 1
            except Exception:
                resumo.n_passo_erros += 1

        if resumo.ts_inicio_ns > 0:
            resumo.span_s = (resumo.ts_fim_ns - resumo.ts_inicio_ns) / 1e9

        return resumo
