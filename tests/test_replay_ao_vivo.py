"""Teste do mesmo caminho: replay e backtest leem a mesma gravação e concordam.

Três invariantes:
1. A série E18 capturada pelo ReplayCiclo é idêntica à série do caminho direto
   — mesmos parsers, mesma sequência, sem divergência de arredondamento.
2. Os timestamps de agora_ns passados ao ciclo vêm do relógio da GRAVAÇÃO, não
   de time.time_ns() — validado verificando que ts_inicio_ns cai em 2026-08-24.
3. O discovery_snapshot alimenta o rastreador — o ciclo recebe janelas reais.

Estes testes só rodam quando a gravação real está presente. Em CI (sem
pulsearb-dados/) são ignorados automaticamente.
"""

from __future__ import annotations

import pathlib

import pytest

from pulsearb.execution import ExecutorSombra
from pulsearb.feeds.rtds import TOPIC_TWAP_60, e18_do_evento, parse_rtds_event
from pulsearb.live.ciclo import FONTE_RTDS, CicloAoVivo
from pulsearb.live.livros import LivrosAoVivo
from pulsearb.live.motor import ConfigDoMotor, MotorAoVivo
from pulsearb.live.precos import PrecosAoVivo
from pulsearb.live.rastreador import RastreadorDeJanelas
from pulsearb.replay.ao_vivo import ReplayCiclo
from pulsearb.replay.player import ReplayPlayer
from pulsearb.replay.reader import RecordingReader
from pulsearb.risk import PortaoDeRisco
from pulsearb.settings import Mode, RiskSettings

DADOS = pathlib.Path.home() / "pulsearb-dados"
ARQUIVO = DADOS / "pulsearb-20260824-2000.jsonl.gz"

pytestmark = pytest.mark.skipif(
    not ARQUIVO.exists(), reason="gravação real ausente (~/pulsearb-dados/)"
)

# 2026-08-24 00:00 UTC e 2026-08-25 00:00 UTC em nanosegundos.
# Validam que agora_ns veio do relógio da gravação, não de time.time_ns().
_DIA_24_NS = 1_787_529_600_000_000_000
_DIA_25_NS = 1_787_616_000_000_000_000

# Processa apenas 5 minutos do início da gravação para que os testes sejam
# rápidos (o arquivo tem ~400 MB comprimido). Cinco minutos trazem centenas de
# ticks por ativo — suficiente para validar parser, relógio e discovery.
_JANELA_S = 5 * 60

_ATIVOS = frozenset(["bnb", "btc", "doge", "eth", "hype", "sol", "xrp", "zec"])


def _ate_ns(arquivo: pathlib.Path) -> int:
    """Retorna ts_wall_ns do primeiro registro + _JANELA_S segundos."""
    for record in RecordingReader([arquivo]).iter_records():
        return record.ts_wall_ns + int(_JANELA_S * 1e9)
    return 0


def _ciclo(tmp_path: pathlib.Path) -> CicloAoVivo:
    precos = PrecosAoVivo()
    portao = PortaoDeRisco(
        RiskSettings(),
        Mode.SHADOW,
        caminho_do_registro=tmp_path / "registro.json",
        relogio_do_servidor=precos.relogio,
    )
    motor = MotorAoVivo(
        rastreador=RastreadorDeJanelas(),
        livros=LivrosAoVivo(),
        precos=precos,
        executor=ExecutorSombra(portao, caminho_do_diario=tmp_path / "diario.jsonl"),
        config=ConfigDoMotor(),
    )
    return CicloAoVivo(motor=motor, ativos_operados=_ATIVOS)


def _series_diretas(
    arquivo: pathlib.Path, *, ate_ns: int
) -> dict[str, list[tuple[int, int]]]:
    """Caminho direto: mesmos parsers que o ciclo, sem passar pelo CicloAoVivo."""
    series: dict[str, list[tuple[int, int]]] = {}
    for record in RecordingReader([arquivo]).iter_records():
        if record.ts_wall_ns > ate_ns:
            break
        if record.is_meta:
            continue
        event = ReplayPlayer._to_feed_event(record)
        if event.source != FONTE_RTDS:
            continue
        tick = parse_rtds_event(event.parsed, event.ts_mono_ns, event.ts_wall_ns)
        if tick is None or tick.topic != TOPIC_TWAP_60 or tick.src_timestamp_ms <= 0:
            continue
        valor = e18_do_evento(event.parsed)
        if valor is None:
            continue
        series.setdefault(tick.asset, []).append((record.ts_wall_ns, valor))
    return series


class TestMesmoCaminho:
    def test_series_de_preco_identicas_ao_caminho_direto(self, tmp_path):
        """ReplayCiclo e caminho direto veem exatamente os mesmos preços."""
        ate = _ate_ns(ARQUIVO)
        ciclo = _ciclo(tmp_path)
        resumo = ReplayCiclo([ARQUIVO], ciclo, ate_ns=ate).executar()
        diretas = _series_diretas(ARQUIVO, ate_ns=ate)

        assert resumo.series_e18, "nenhum preço E18 capturado pelo replay"
        assert set(resumo.series_e18.keys()) == set(diretas.keys()), (
            f"ativos divergem: replay={set(resumo.series_e18)}, direto={set(diretas)}"
        )
        for asset in diretas:
            assert resumo.series_e18[asset] == diretas[asset], (
                f"{asset}: {len(resumo.series_e18[asset])} pontos no replay vs "
                f"{len(diretas[asset])} no direto — parser ou relógio divergiu"
            )

    def test_ciclo_avancou_o_relogio_com_timestamps_da_gravacao(self, tmp_path):
        """agora_ns veio do relógio da gravação (2026-08-24), não de time.time_ns()."""
        ate = _ate_ns(ARQUIVO)
        ciclo = _ciclo(tmp_path)
        resumo = ReplayCiclo([ARQUIVO], ciclo, ate_ns=ate).executar()

        assert resumo.ts_inicio_ns > 0, "nenhum registro processado"
        assert _DIA_24_NS <= resumo.ts_inicio_ns < _DIA_25_NS, (
            f"ts_inicio fora de 2026-08-24: {resumo.ts_inicio_ns / 1e9:.0f} s — "
            f"agora_ns pode estar vindo de time.time_ns() em vez da gravação"
        )
        assert _DIA_24_NS <= resumo.ts_fim_ns < _DIA_25_NS, (
            f"ts_fim fora de 2026-08-24: {resumo.ts_fim_ns / 1e9:.0f} s"
        )
        assert resumo.span_s > 60, (
            f"span {resumo.span_s:.0f}s < 60 s — muito poucas gravações ou "
            f"leitura interrompida antes do fim da janela de 5 min"
        )

    def test_discovery_alimenta_o_rastreador(self, tmp_path):
        """discovery_snapshot na gravação chega ao rastreador via on_descoberta."""
        ate = _ate_ns(ARQUIVO)
        ciclo = _ciclo(tmp_path)
        resumo = ReplayCiclo([ARQUIVO], ciclo, ate_ns=ate).executar()

        assert resumo.n_discovery > 0, (
            "nenhum discovery_snapshot nos primeiros 5 min — a gravação pode "
            "não ter snapshots nesta janela, ou FONTE_DISCOVERY está errado"
        )
        # on_descoberta incrementa ciclo.contagem["descoberta"].
        assert ciclo.contagem.get("descoberta", 0) == resumo.n_discovery, (
            f"n_discovery={resumo.n_discovery} mas ciclo.contagem[descoberta]="
            f"{ciclo.contagem.get('descoberta', 0)} — descobertas perdidas"
        )
