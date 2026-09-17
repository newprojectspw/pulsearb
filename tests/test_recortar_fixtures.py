"""`scripts/recortar_fixtures.py` — o extractor, sobre gravação sintética.

A gravação daqui é sintética DE PROPÓSITO: testa o recorte (classificação,
teto por tipo, teto de bytes, manifesto, contenção da saída), não o
servidor. O que prova o servidor é `tests/test_fixtures_reais.py`.
"""

from __future__ import annotations

import gzip
import importlib.util
import json
from pathlib import Path

from pulsearb.replay.reader import RecordingReader

RAIZ = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "recortar_fixtures", RAIZ / "scripts" / "recortar_fixtures.py"
)
assert _spec is not None and _spec.loader is not None
rf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rf)

T0 = 1_757_400_000_000_000_000  # 2026-09-09 ~ UTC
TWAP = {"topic": "crypto_prices_twap_sixty", "payload": {"symbol": "btc/usd"}}


def _reg(i: int, fonte: str, payload):
    return {"ts_mono_ns": i, "ts_wall_ns": T0 + i * 1_000_000, "fonte": fonte, "payload": payload}


def _gravar(pasta: Path, registros, *, gz: bool = False) -> Path:
    pasta.mkdir(exist_ok=True)
    texto = "\n".join(json.dumps(r) for r in registros) + "\n"
    if gz:
        alvo = pasta / "pulsearb-20260909-02.jsonl.gz"
        alvo.write_bytes(gzip.compress(texto.encode()))
    else:
        alvo = pasta / "pulsearb-20260909-02.jsonl"
        alvo.write_text(texto, encoding="utf-8")
    return pasta


def _registros():
    regs = []
    for i in range(5):
        regs.append(_reg(10 + i, "poly_ws", {"event_type": "price_change", "asset_id": "a"}))
    regs.append(_reg(20, "poly_ws", [{"event_type": "book"}, {"event_type": "last_trade_price"}]))
    regs.append(_reg(30, "rtds", TWAP))
    regs.append(_reg(31, "rtds", {"topic": "crypto_prices", "payload": {}}))
    regs.append(_reg(40, "discovery_snapshot", {"markets": []}))  # meta: fora
    regs.append(_reg(41, "poly_ws", {"sem_event_type": True}))    # inclassificável
    return regs


class TestRecorte:
    def test_classifica_por_fonte_e_tipo_e_um_lote_conta_para_cada_tipo(self, tmp_path):
        reader = RecordingReader(_gravar(tmp_path / "g", _registros()))
        rec = rf.recortar(reader)
        assert set(rec) == {
            "poly_ws/price_change", "poly_ws/book", "poly_ws/last_trade_price",
            "rtds/crypto_prices_twap_sixty", "rtds/crypto_prices",
        }
        assert len(rec["poly_ws/price_change"]["linhas"]) == 5
        assert rec["poly_ws/price_change"]["vistos"] == 5
        # O mesmo registro aparece nos dois tipos que carrega.
        assert rec["poly_ws/book"]["linhas"] == rec["poly_ws/last_trade_price"]["linhas"]

    def test_teto_por_tipo_guarda_os_primeiros_e_segue_contando_os_vistos(self, tmp_path):
        reader = RecordingReader(_gravar(tmp_path / "g", _registros(), gz=True))
        rec = rf.recortar(reader, por_tipo=2)
        bloco = rec["poly_ws/price_change"]
        assert len(bloco["linhas"]) == 2
        assert bloco["vistos"] == 5
        assert [json.loads(x)["ts_mono_ns"] for x in bloco["linhas"]] == [10, 11]

    def test_teto_de_bytes_nunca_deixa_um_tipo_vazio(self, tmp_path):
        reader = RecordingReader(_gravar(tmp_path / "g", _registros()))
        rec = rf.recortar(reader, max_bytes_por_tipo=1)
        assert all(len(b["linhas"]) == 1 for b in rec.values())

    def test_a_linha_gravada_e_o_registro_inteiro_como_o_recorder_escreveu(self, tmp_path):
        reader = RecordingReader(_gravar(tmp_path / "g", _registros()))
        rec = rf.recortar(reader)
        linha = json.loads(rec["rtds/crypto_prices_twap_sixty"]["linhas"][0])
        assert linha == _reg(30, "rtds", TWAP)


class TestMain:
    def test_grava_um_jsonl_por_tipo_e_o_manifesto_dentro_da_raiz(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("PULSEARB_BACKTEST_OUTPUT_ROOT", raising=False)
        _gravar(tmp_path / "g", _registros())
        (tmp_path / "saida").mkdir()

        argv = ["g", "--saida", "saida", "--por-tipo", "3", "--desde", "2026-09-09T00:00Z"]
        assert rf.main(argv) == 0

        manifesto = json.loads((tmp_path / "saida" / rf.MANIFESTO).read_text())
        assert manifesto["origem"]["gravacao"] == "g"
        assert manifesto["tipos"]["poly_ws/price_change"]["registros"] == 3
        assert manifesto["tipos"]["poly_ws/price_change"]["vistos_no_periodo"] == 5
        arquivo = tmp_path / "saida" / manifesto["tipos"]["poly_ws/price_change"]["arquivo"]
        assert arquivo.name == "poly_ws__price_change.jsonl"
        assert len(arquivo.read_text().splitlines()) == 3
        assert (tmp_path / "saida" / "rtds__crypto_prices_twap_sixty.jsonl").exists()

    def test_recusa_saida_absoluta_ou_inexistente_antes_de_ler(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("PULSEARB_BACKTEST_OUTPUT_ROOT", raising=False)
        _gravar(tmp_path / "g", _registros())
        assert rf.main(["g", "--saida", "/tmp/fixtures"]) == 2
        assert rf.main(["g", "--saida", "nao-existe"]) == 2
        assert rf.main(["g", "--saida", ".", "--por-tipo", "0"]) == 2
        assert "diretório de saída não existe" in capsys.readouterr().err

    def test_gravacao_sem_registro_classificavel_devolve_1(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _gravar(tmp_path / "g", [_reg(1, "discovery_snapshot", {})])
        (tmp_path / "saida").mkdir()
        assert rf.main(["g", "--saida", "saida"]) == 1


_spec_fr = importlib.util.spec_from_file_location(
    "test_fixtures_reais", RAIZ / "tests" / "test_fixtures_reais.py"
)
assert _spec_fr is not None and _spec_fr.loader is not None
fr = importlib.util.module_from_spec(_spec_fr)
_spec_fr.loader.exec_module(fr)

TOK = "0x" + "a" * 64


def _price_change_gravado(ts, bid, ask):
    """Forma B do servidor (`price_changes` com best_bid/best_ask), API_NOTES §6.1b."""
    return _reg(ts, "poly_ws", {"event_type": "price_change", "price_changes": [
        {"asset_id": TOK, "price": "0.50", "size": "1", "side": "BUY",
         "best_bid": str(bid), "best_ask": str(ask)}]})


class TestConsumidorSobreRecorte:
    """`tests/test_fixtures_reais.py` só vale se FALHAR quando o parser lê zero.

    Aqui ele roda sobre um recorte produzido pelo extractor: uma vez com a
    forma gravada (passa), outra com uma forma que o parser não lê (falha).
    Sem isto, o consumidor seria um teste que passa por definição.
    """

    def _recorte(self, tmp_path, monkeypatch, registros):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("PULSEARB_BACKTEST_OUTPUT_ROOT", raising=False)
        _gravar(tmp_path / "g", registros)
        (tmp_path / "reais").mkdir()
        assert rf.main(["g", "--saida", "reais"]) == 0
        monkeypatch.setattr(fr, "PASTA", tmp_path / "reais")
        monkeypatch.setattr(fr, "MANIFESTO", tmp_path / "reais" / rf.MANIFESTO)

    def test_passa_sobre_a_forma_gravada(self, tmp_path, monkeypatch):
        book = _reg(2, "poly_ws", {"event_type": "book", "asset_id": TOK,
                                   "bids": [{"price": "0.49", "size": "10"}],
                                   "asks": [{"price": "0.51", "size": "10"}]})
        print_ = _reg(3, "poly_ws", {"event_type": "last_trade_price", "asset_id": TOK,
                                     "price": "0.51", "side": "BUY", "size": "5"})
        self._recorte(tmp_path, monkeypatch, [_price_change_gravado(1, 0.49, 0.51), book, print_])
        fr.TestPolyWs().test_book_real_tem_forma_conhecida()
        fr.TestPolyWs().test_last_trade_price_real_tem_preco_e_side()
        fr.TestPolyWs().test_price_change_real_tem_forma_conhecida_e_topo_legivel()
        fr.TestPolyWs().test_todo_registro_poly_ws_rende_ao_menos_um_evento()

    def test_falha_quando_o_parser_le_zero(self, tmp_path, monkeypatch):
        import pytest

        sem_lista = _reg(1, "poly_ws", {"event_type": "price_change", "asset_id": TOK,
                                        "price": "0.50", "side": "BUY"})
        self._recorte(tmp_path, monkeypatch, [sem_lista])
        with pytest.raises(AssertionError, match="sem lista"):
            fr.TestPolyWs().test_price_change_real_tem_forma_conhecida_e_topo_legivel()

    def test_salta_quando_o_tipo_nao_esta_no_recorte(self, tmp_path, monkeypatch):
        import pytest

        self._recorte(tmp_path, monkeypatch, [_price_change_gravado(1, 0.49, 0.51)])
        with pytest.raises(pytest.skip.Exception, match="market_resolved"):
            fr.TestPolyWs().test_market_resolved_real_e_lido_pelo_parser()
