"""O instrumento que mede V(t), validado contra processos de resposta conhecida.

Um instrumento de medição que ninguém calibrou é o defeito que este projeto já
pagou duas vezes: o `erro` que não media calibração (M2.13) e o
`cobertura_da_gravacao` que dizia 1,0 num relatório com 3.601 s de silêncio.
Por isso aqui o instrumento é apontado para duas séries construídas, cuja
variância se conhece de antemão, ANTES de ser apontado para a gravação.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

from pulsearb.analysis.variancia_de_transicao import (
    curva_de_variancia,
    fator_do_modelo,
    veredito_da_curva,
)
from pulsearb.engine.twap import variance_factor

JANELA = 60


def _caminhada(n: int, sigma: float, semente: int) -> list[float]:
    rng = random.Random(semente)
    preco = 100.0
    saida = []
    for _ in range(n):
        preco *= 1.0 + rng.gauss(0.0, sigma)
        saida.append(preco)
    return saida


def _serie(valores: list[float], passo_s: float = 1.0) -> list[tuple[int, float]]:
    return [(int(i * passo_s * 1e9), v) for i, v in enumerate(valores)]


def _suavizada(brutos: list[float], janela: int = JANELA) -> list[float]:
    """Média móvel de `janela` pontos — um `twap_sixty` sintético."""
    return [
        sum(brutos[i - janela + 1 : i + 1]) / janela for i in range(janela - 1, len(brutos))
    ]


def _linha(curva, h):
    return next(x for x in curva["horizontes"] if x["horizonte_s"] == h)


# --------------------------------------------------------- o fator do modelo
def test_fator_do_modelo_degenera_no_variance_factor_abaixo_da_janela():
    for t in (5, 30, 59):
        assert fator_do_modelo(t) == pytest.approx(variance_factor(t))
    # Acima da janela entra o tempo de espera, que era o defeito da §2d-ter.
    assert fator_do_modelo(240) == pytest.approx(180 + variance_factor(60))


# ------------------------------------------------- caminhada aleatória pura
def test_caminhada_pura_tem_variancia_por_segundo_constante():
    """A referência. Sem suavização, V(t)/t não depende de t."""
    curva = curva_de_variancia(_serie(_caminhada(14_400, 2e-4, semente=11)))

    por_segundo = [
        x["variancia_por_segundo"] for x in curva["horizontes"] if x["suficiente"]
    ]
    assert max(por_segundo) / min(por_segundo) < 1.6, por_segundo

    veredito = veredito_da_curva(curva)
    assert veredito["monotona"]
    assert not veredito["ha_suavizacao"], veredito


def test_caminhada_pura_confirma_o_fator_corrigido_do_modelo():
    """Com preço BRUTO na entrada, o modelo corrigido acerta a magnitude.

    É o outro lado da §2d-ter: a correção da variância está certa *para o
    observável que o modelo supõe*. O que a §2d-ter registra é que o
    observável do pipeline não é esse.
    """
    curva = curva_de_variancia(_serie(_caminhada(14_400, 2e-4, semente=12)))
    for h in (120, 240, 300):
        razao = _linha(curva, h)["razao_contra_o_modelo"]
        assert 0.6 < razao < 1.7, (h, razao)


# ------------------------------------------------------- série JÁ suavizada
def test_serie_suavizada_denuncia_a_suavizacao():
    """O caso do `twap_sixty`: a entrada já é média de 60 s.

    A variância de 1 s fica esmagada pela suavização, mas a de horizonte longo
    volta a ser a do subjacente. `V(t)/t` cresce, e é essa subida que o
    `veredito_da_curva` chama de suavização.
    """
    brutos = _caminhada(20_000, 2e-4, semente=13)
    curva = curva_de_variancia(_serie(_suavizada(brutos)))

    veredito = veredito_da_curva(curva)
    assert veredito["ha_suavizacao"], veredito
    assert veredito["fator_de_suavizacao_medido"] > 5, veredito

    curto = _linha(curva, 1)["variancia_por_segundo"]
    longo = _linha(curva, 300)["variancia_por_segundo"]
    assert longo > 10 * curto, (curto, longo)


def test_serie_suavizada_mostra_o_tamanho_do_erro_do_modelo():
    """O número que decide a §2d-ter: quanto o modelo erra com esta entrada.

    Alimentado com a série suavizada — que é o que o pipeline faz hoje —, o
    `sigma_1s` é a volatilidade da média móvel, e multiplicá-lo pelo fator de
    caminhada bruta subestima a variância por uma ordem de grandeza. O teste
    não crava o valor (ele depende da forma exata da suavização); crava que é
    grande, que é o que muda a conclusão.
    """
    brutos = _caminhada(20_000, 2e-4, semente=14)
    curva = curva_de_variancia(_serie(_suavizada(brutos)))

    for h in (120, 240, 300):
        razao = _linha(curva, h)["razao_contra_o_modelo"]
        assert razao > 10, (h, razao)


# ----------------------------------------------------------------- higiene
def test_pares_fora_do_horizonte_nao_entram():
    """Tolerância recusa o par que não está à distância pedida.

    Sem isso, uma lacuna de feed faria pares de 300 s serem contados como
    pares de 240 s, e a curva mediria o horizonte errado — que é a mesma
    classe do defeito que a §2d-bis achou no 1.4.
    """
    valores = _caminhada(4_000, 2e-4, semente=15)
    # Passo de 7 s: nenhum par cai a 240 s (múltiplos de 7 pulam 238 e 245),
    # e a tolerância de 1 s não alcança.
    curva = curva_de_variancia(_serie(valores, passo_s=7.0), horizontes_s=(240,))
    assert not _linha(curva, 240)["suficiente"]


def test_lacuna_no_meio_nao_invalida_o_par():
    """Par com os extremos à distância certa vale, mesmo sem os pontos do meio.

    O preço andou; nós é que não vimos o caminho. Recusar esses pares
    descartaria justamente os períodos de silêncio do feed, e o Bloco 0 mostrou
    que eles não são raros.
    """
    valores = _caminhada(6_000, 2e-4, semente=16)
    serie = _serie(valores)
    # Tira o miolo de cada bloco de 100 s, preservando as pontas.
    ralo = [par for i, par in enumerate(serie) if i % 100 in (0, 60)]
    curva = curva_de_variancia(ralo, horizontes_s=(60,), minimo_de_pares=50)
    assert _linha(curva, 60)["suficiente"]
    assert _linha(curva, 60)["n"] >= 50


def test_horizonte_sem_pares_suficientes_nao_e_reportado_como_medido():
    curva = curva_de_variancia(_serie(_caminhada(300, 2e-4, semente=17)))
    assert not _linha(curva, 600)["suficiente"]
    assert _linha(curva, 600)["variancia"] is None


# ------------------------------------------------- o script, ponta a ponta
def test_script_le_a_gravacao_e_mede(tmp_path, monkeypatch):
    """O caminho inteiro: gravação no disco → curva por ativo.

    Sem isto, o módulo estaria testado e o script — que é o que roda de
    verdade na máquina de análise — não. Foi assim que o `analisa_dia.sh`
    anunciou `rodando` para um processo morto.
    """
    from tests.synthetic import gerar_gravacao

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import variancia_de_transicao as script

    gerar_gravacao(tmp_path / "rec" / "gravacao.jsonl.gz", n_janelas=8, duracao_s=300)

    series, descartes = script.series_da_gravacao(tmp_path / "rec", progresso=False)
    assert series, "nenhum tick de twap_sixty encontrado na gravacao sintetica"
    assert not descartes, descartes

    relatorio = script.medir(series, horizontes_s=(2, 5, 10, 30, 60))
    assert relatorio["ativos"] >= 1
    for curva in relatorio["por_ativo"].values():
        medidos = [x for x in curva["horizontes"] if x["suficiente"]]
        assert medidos, curva
        # Variância cresce com o horizonte, em qualquer processo de preço.
        assert curva["veredito"]["monotona"]


# ------------------------------------- o veredito nao inventa o que nao mediu
def test_sem_horizonte_longo_o_veredito_nao_e_avaliavel():
    """Ausência de evidência não pode sair como evidência de ausência.

    É o defeito do `cobertura_da_gravacao` reportando 1,0 num relatório com
    3.601 s de silêncio, e o do `erro` que um preditor constante gabaritava.
    Aqui ele apareceria assim: gravação curta demais para medir 240 s, e o
    relatório dizendo `ha_suavizacao: false` como se tivesse medido.
    """
    curva = curva_de_variancia(
        _serie(_caminhada(3_000, 2e-4, semente=21)), horizontes_s=(2, 5, 10, 30, 60)
    )
    veredito = veredito_da_curva(curva)

    assert veredito["avaliavel"] is False
    assert veredito["ha_suavizacao"] is None
    assert veredito["fator_de_suavizacao_medido"] is None


def test_um_horizonte_longo_so_nao_basta_para_julgar_linearidade():
    """Com um ponto no regime longo não há reta para conferir."""
    curva = curva_de_variancia(
        _serie(_caminhada(20_000, 2e-4, semente=22)), horizontes_s=(10, 30, 60, 240)
    )
    assert veredito_da_curva(curva)["avaliavel"] is False


def test_serie_com_momento_nao_e_rotulada_de_suavizacao():
    """V(t)/t subindo sem parar é OUTRO processo, não suavização.

    A §2d-ter registrou três propriedades e a primeira versão do veredito só
    conferia duas — a linearidade no regime longo ficou de fora, e o docstring
    dizia que conferia as três.

    Aqui os retornos são autocorrelacionados (AR(1) com φ = 0,998, memória de
    ~500 s): a série tem momento, `V(t)/t` cresce em TODO horizonte, e a razão
    longo/curto passa de 400. Sem a checagem de linearidade isso sairia como
    "suavização, fator 428", que é atribuir o processo errado.

    Limite conhecido desta checagem, registrado para não virar promessa: um
    processo de memória CURTA (φ = 0,99, ~100 s) já está no regime assintótico
    entre 240 e 600 s, passa na linearidade e é rotulado de suavização. A
    checagem separa o que se parece com suavização no horizonte medido — não
    prova a origem física do achatamento.
    """
    rng = random.Random(31)
    retorno, preco, valores = 0.0, 100.0, []
    for _ in range(20_000):
        retorno = 0.998 * retorno + rng.gauss(0.0, 2e-5)
        preco *= 1.0 + retorno
        valores.append(preco)

    veredito = veredito_da_curva(curva_de_variancia(_serie(valores)))
    assert veredito["avaliavel"]
    assert veredito["fator_de_suavizacao_medido"] > 100, veredito
    assert veredito["linear_no_longo"] is False, veredito
    assert veredito["ha_suavizacao"] is False, veredito


def test_concordancia_nao_conta_ativo_que_nao_deu_para_avaliar():
    """Um ativo sem amostra não vota — nem a favor nem contra."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import variancia_de_transicao as script

    longa = _serie(_caminhada(20_000, 2e-4, semente=24))
    curta = _serie(_caminhada(400, 2e-4, semente=25))
    relatorio = script.medir({"btc": longa, "sol": curta})

    concordancia = relatorio["concordam_sobre_suavizacao"]
    assert concordancia["avaliados"] == 1
    assert concordancia["sem_amostra_para_avaliar"] == 1


# ------------------------------------------ o recorte por dia, sem margem
def test_dia_abre_as_horas_de_borda_e_decide_pelo_relogio_de_origem(tmp_path):
    """A curva que calibra o dia 24 não pode conter tick nenhum do dia 24.

    O nome do arquivo é aproximação — o `RecordingReader` documenta que um
    evento de 13:59:59,9 pode estar no arquivo das 14h. Então a seleção por
    nome abre as horas de BORDA (23h do dia anterior, 00h do seguinte) e quem
    decide de fato é o relógio de ORIGEM do tick.

    Ficar só nos nomes deixaria entrar tick do dia seguinte e sairia tick do
    dia pedido — o vazamento que o `--dia` existe para impedir (achado em
    review).
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import variancia_de_transicao as script

    for nome in (
        "pulsearb-20260822-2300.jsonl.gz",
        "pulsearb-20260823-0000.jsonl.gz",
        "pulsearb-20260823-2300.jsonl.gz",
        "pulsearb-20260824-0000.jsonl.gz",
        "pulsearb-20260824-0100.jsonl.gz",
    ):
        (tmp_path / nome).write_bytes(b"")

    todos = sorted(tmp_path.glob("*.jsonl.gz"))
    escolhidos = [p.name for p in script.arquivos_do_dia(todos, "20260823")]

    # As duas horas do dia, mais as duas bordas — e nada além disso.
    assert escolhidos == [
        "pulsearb-20260822-2300.jsonl.gz",
        "pulsearb-20260823-0000.jsonl.gz",
        "pulsearb-20260823-2300.jsonl.gz",
        "pulsearb-20260824-0000.jsonl.gz",
    ]
    assert "pulsearb-20260824-0100.jsonl.gz" not in escolhidos

    # E o relógio de origem é quem separa de verdade.
    from datetime import UTC, datetime

    def ms(iso: str) -> int:
        return int(datetime.fromisoformat(iso).replace(tzinfo=UTC).timestamp() * 1000)

    assert script.ticks_do_dia("20260823", ms("2026-08-23T00:00:00"))
    assert script.ticks_do_dia("20260823", ms("2026-08-23T23:59:59"))
    # Um tick do dia 24 dentro do arquivo de borda do dia 23: FORA.
    assert not script.ticks_do_dia("20260823", ms("2026-08-24T00:00:00"))
    assert not script.ticks_do_dia("20260823", ms("2026-08-22T23:59:59"))


def test_dia_sem_arquivo_falha_alto(tmp_path):
    """Dia errado tem de parar, não medir a gravação inteira em silêncio."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import variancia_de_transicao as script

    with pytest.raises(SystemExit, match="20260101"):
        script.series_da_gravacao(tmp_path, progresso=False, dia="20260101")


def test_dia_hostil_e_recusado_antes_do_glob(tmp_path):
    """`--dia` é interpolado num glob, então é entrada que chega ao disco.

    Sem a trava, `--dia ../../etc` produziria o padrão
    `pulsearb-../../etc-[0-9][0-9][0-9][0-9].jsonl*` e a busca sairia da
    raiz. Mesma travessia que o M2.5 fechou no `--json`.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import variancia_de_transicao as script

    for hostil in ("../../etc", "2026*", "20260823/..", "", "2026082", "abcdefgh"):
        with pytest.raises(ValueError, match="dia inválido"):
            script.arquivos_do_dia([], hostil)

    # E o válido continua passando.
    valido = [tmp_path / "pulsearb-20260823-0000.jsonl.gz"]
    assert len(script.arquivos_do_dia(valido, "20260823")) == 1


def test_sem_dia_o_relatorio_sai_marcado_como_exploratorio(tmp_path, capsys):
    """O comando documentado tem de produzir relatório utilizável.

    Achado em review: o docstring do módulo mostrava o comando SEM `--dia`, e
    o relatório dele é justamente o que o backtest recusa. Quem seguisse a
    documentação descobriria isso três horas depois. Agora o aviso sai na
    hora, e `dia_medido: null` diz o que o relatório é.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from tests.synthetic import gerar_gravacao

    import variancia_de_transicao as script

    diretorio = tmp_path / "rec"
    diretorio.mkdir()
    gerar_gravacao(diretorio / "rec.jsonl.gz", n_janelas=8)

    saida = tmp_path / "relatorios"
    saida.mkdir()
    codigo = script.main(
        [str(diretorio), "--sem-progresso", "--json", "relatorios/exploratorio.json"]
    )
    assert codigo == 0
    assert "EXPLORATORIO" in capsys.readouterr().err
