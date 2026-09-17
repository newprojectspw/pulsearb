"""As duas identidades, e a trava que impede a arbitragem virar aposta.

## O que estes testes guardam

A aritmética aqui é pura e pode ser testada com livros montados à mão SEM
cair no defeito que o CLAUDE.md nomeia — a fixture não encoda suposição
nenhuma sobre o que o servidor manda, porque a conta é sobre preços e
tamanhos, não sobre a forma de um payload. O que é fato de API (se um
conjunto neg-risk é mesmo exaustivo) está FORA deste módulo de propósito, e
quem chama tem de provar.

A propriedade mais importante não é o lucro: é `conjunto_nao_exaustivo`. Um
conjunto a que falte um resultado não paga 1,00, a conta fecha bonita do
mesmo jeito, e a posição vira DIRECIONAL — exatamente o que estas rotas
existem para não ser. O erro seria silencioso.
"""

from __future__ import annotations

import pytest

from pulsearb.analysis.arbitragem import (
    MOTIVOS,
    Taxa,
    maior_cesta,
    oportunidade_de_cesta,
    oportunidade_de_escada,
)
from pulsearb.backtest.book import OrderBook

#: A taxa verificada ao vivo (§12.6): r=0,07, e=1. Não há default no módulo
#: de fees de propósito — aqui ela é explícita porque o teste é o chamador.
TAXA = Taxa(rate=0.07, exponent=1.0)
#: Uma taxa ZERO para os testes que medem a identidade sem o ruído da fee.
SEM_TAXA = Taxa(rate=0.0, exponent=1.0)


def _livro(asks=(), bids=(), asset_id="tok"):
    return OrderBook(asset_id=asset_id, asks=list(asks), bids=list(bids))


class TestACestaNegRisk:
    def test_cesta_abaixo_de_um_da_lucro_igual_a_folga(self):
        """Três resultados a 0,30 cada: a cesta custa 0,90 e paga 1,00."""
        livros = [_livro(asks=[(0.30, 100.0)], asset_id=f"t{i}") for i in range(3)]

        op, motivo = oportunidade_de_cesta(
            livros, [SEM_TAXA] * 3, shares=10.0, conjunto_exaustivo=True
        )

        assert motivo is None
        assert op.custo_usdc == pytest.approx(9.0)
        assert op.lucro_usdc == pytest.approx(1.0)
        assert op.lucro_por_share == pytest.approx(0.10)

    def test_cesta_acima_de_um_NAO_e_oportunidade(self):
        livros = [_livro(asks=[(0.35, 100.0)], asset_id=f"t{i}") for i in range(3)]

        op, motivo = oportunidade_de_cesta(
            livros, [SEM_TAXA] * 3, shares=10.0, conjunto_exaustivo=True
        )

        assert op is None and motivo == "sem_folga"

    def test_a_taxa_e_DESCONTADA_e_pode_matar_a_folga(self):
        """0,98 de custo com folga de 0,02 — e a fee em p=0,49 come mais que
        isso. Arbitragem que ignora a fee é arbitragem inventada."""
        livros = [_livro(asks=[(0.49, 100.0)], asset_id=f"t{i}") for i in range(2)]

        com, _ = oportunidade_de_cesta(
            livros, [SEM_TAXA] * 2, shares=10.0, conjunto_exaustivo=True
        )
        sem, motivo = oportunidade_de_cesta(
            livros, [TAXA] * 2, shares=10.0, conjunto_exaustivo=True
        )

        assert com is not None and com.lucro_usdc == pytest.approx(0.2)
        assert sem is None and motivo == "sem_folga"

    def test_atravessar_o_livro_encarece_e_o_lucro_por_share_CAI(self):
        """É por isso que o tamanho entra no resultado, e não só a existência."""
        livros = [
            _livro(asks=[(0.30, 5.0), (0.34, 100.0)], asset_id=f"t{i}")
            for i in range(3)
        ]

        pequena, _ = oportunidade_de_cesta(
            livros, [SEM_TAXA] * 3, shares=5.0, conjunto_exaustivo=True
        )
        grande, _ = oportunidade_de_cesta(
            livros, [SEM_TAXA] * 3, shares=10.0, conjunto_exaustivo=True
        )

        assert pequena.lucro_por_share > grande.lucro_por_share
        assert grande.niveis_atravessados > pequena.niveis_atravessados


class TestATravaQueImpedeViraraAposta:
    """Sem a exaustividade, a cesta não paga 1,00 e isso NÃO dá erro sozinho."""

    def test_conjunto_nao_exaustivo_RECUSA_mesmo_com_conta_lucrativa(self):
        livros = [_livro(asks=[(0.30, 100.0)], asset_id=f"t{i}") for i in range(3)]

        op, motivo = oportunidade_de_cesta(
            livros, [SEM_TAXA] * 3, shares=10.0, conjunto_exaustivo=False
        )

        assert op is None and motivo == "conjunto_nao_exaustivo"

    def test_perna_que_nao_enche_RECUSA_em_vez_de_encher_parcial(self):
        """Uma perna faltando desfaz a identidade: o que sobra é posição
        direcional, o oposto do que esta rota existe para ser."""
        livros = [
            _livro(asks=[(0.30, 100.0)], asset_id="t0"),
            _livro(asks=[(0.30, 2.0)], asset_id="t1"),
        ]

        op, motivo = oportunidade_de_cesta(
            livros, [SEM_TAXA] * 2, shares=10.0, conjunto_exaustivo=True
        )

        assert op is None and motivo == "livro_raso"

    def test_perna_sem_livro_RECUSA(self):
        livros = [_livro(asks=[(0.30, 100.0)], asset_id="t0"), _livro(asset_id="t1")]

        op, motivo = oportunidade_de_cesta(
            livros, [SEM_TAXA] * 2, shares=10.0, conjunto_exaustivo=True
        )

        assert op is None and motivo == "perna_sem_livro"


class TestAMaiorCesta:
    def test_devolve_o_MAXIMO_e_nao_o_primeiro_tamanho_que_da_lucro(self):
        """O lucro por share cai com o tamanho, mas o TOTAL sobe até o livro
        acabar. Reportar tamanho 1 diria 'existe' sobre algo que rende
        centavos."""
        livros = [
            _livro(asks=[(0.30, 20.0), (0.40, 100.0)], asset_id=f"t{i}")
            for i in range(3)
        ]

        op, motivo = maior_cesta(
            livros, [SEM_TAXA] * 3, conjunto_exaustivo=True, teto_de_shares=50.0
        )

        assert motivo is None
        # Aos 20 a cesta custa 0,90; passar disso puxa o nível de 0,40 e a
        # cesta passa de 1,00 — o máximo está na virada.
        assert op.shares == pytest.approx(20.0)
        assert op.lucro_usdc == pytest.approx(2.0)

    def test_sem_oportunidade_nenhuma_devolve_motivo(self):
        livros = [_livro(asks=[(0.50, 100.0)], asset_id=f"t{i}") for i in range(3)]

        op, motivo = maior_cesta(
            livros, [SEM_TAXA] * 3, conjunto_exaustivo=True, teto_de_shares=10.0
        )

        assert op is None and motivo == "sem_folga"


class TestAEscadaDeLimiares:
    """`P(X ≥ k₁) ≥ P(X ≥ k₂)`: quem passa de k₂ passou de k₁."""

    def test_comprar_o_limiar_BAIXO_e_vender_o_ALTO_trava_a_diferenca(self):
        baixo = _livro(asks=[(0.40, 100.0)], asset_id="k1")
        alto = _livro(bids=[(0.55, 100.0)], asset_id="k2")

        op, motivo = oportunidade_de_escada(
            baixo, alto, SEM_TAXA, SEM_TAXA,
            shares=10.0, token_baixo="k1", token_alto="k2",
            limiar_baixo=100_000.0, limiar_alto=110_000.0,
        )

        assert motivo is None
        assert op.lucro_usdc == pytest.approx(1.5)

    def test_escada_em_ordem_CORRETA_nao_e_oportunidade(self):
        """O caso normal: o limiar baixo vale MAIS que o alto."""
        baixo = _livro(asks=[(0.60, 100.0)], asset_id="k1")
        alto = _livro(bids=[(0.40, 100.0)], asset_id="k2")

        op, motivo = oportunidade_de_escada(
            baixo, alto, SEM_TAXA, SEM_TAXA,
            shares=10.0, token_baixo="k1", token_alto="k2",
            limiar_baixo=100_000.0, limiar_alto=110_000.0,
        )

        assert op is None and motivo == "sem_folga"

    def test_o_MESMO_token_dos_dois_lados_RECUSA(self):
        """Comprar e vender o mesmo token não é escada — é ruído de
        agrupamento, e daria 'lucro' do spread invertido."""
        livro = _livro(asks=[(0.40, 100.0)], bids=[(0.55, 100.0)], asset_id="k")

        op, motivo = oportunidade_de_escada(
            livro, livro, SEM_TAXA, SEM_TAXA,
            shares=10.0, token_baixo="k", token_alto="k",
            limiar_baixo=100_000.0, limiar_alto=110_000.0,
        )

        assert op is None and motivo == "mesma_perna"

    def test_limiares_TROCADOS_recusam_em_vez_de_inventar_lucro(self):
        """A conta devolveria "lucro travado" sobre posição que perde.

        Os livros não carregam o limiar deles: com os dois trocados a
        desigualdade se inverte e o que sobra é aposta em spread. Mesmo modo
        de falha do `conjunto_nao_exaustivo`.
        """
        baixo = _livro(asks=[(0.40, 100.0)], asset_id="k1")
        alto = _livro(bids=[(0.55, 100.0)], asset_id="k2")

        op, motivo = oportunidade_de_escada(
            baixo, alto, SEM_TAXA, SEM_TAXA,
            shares=10.0, token_baixo="k1", token_alto="k2",
            limiar_baixo=110_000.0, limiar_alto=100_000.0,
        )

        assert op is None and motivo == "escada_fora_de_ordem"

    def test_limiares_IGUAIS_tambem_recusam(self):
        """Mesmo limiar não é escada: não há desigualdade a explorar."""
        baixo = _livro(asks=[(0.40, 100.0)], asset_id="k1")
        alto = _livro(bids=[(0.55, 100.0)], asset_id="k2")

        op, motivo = oportunidade_de_escada(
            baixo, alto, SEM_TAXA, SEM_TAXA,
            shares=10.0, token_baixo="k1", token_alto="k2",
            limiar_baixo=100_000.0, limiar_alto=100_000.0,
        )

        assert op is None and motivo == "escada_fora_de_ordem"

    def test_sem_bids_no_limiar_alto_nao_ha_o_que_vender(self):
        baixo = _livro(asks=[(0.40, 100.0)], asset_id="k1")
        alto = _livro(asks=[(0.55, 100.0)], asset_id="k2")

        op, motivo = oportunidade_de_escada(
            baixo, alto, SEM_TAXA, SEM_TAXA,
            shares=10.0, token_baixo="k1", token_alto="k2",
            limiar_baixo=100_000.0, limiar_alto=110_000.0,
        )

        assert op is None and motivo == "perna_sem_livro"

    def test_venda_que_nao_enche_RECUSA(self):
        baixo = _livro(asks=[(0.40, 100.0)], asset_id="k1")
        alto = _livro(bids=[(0.55, 3.0)], asset_id="k2")

        op, motivo = oportunidade_de_escada(
            baixo, alto, SEM_TAXA, SEM_TAXA,
            shares=10.0, token_baixo="k1", token_alto="k2",
            limiar_baixo=100_000.0, limiar_alto=110_000.0,
        )

        assert op is None and motivo == "livro_raso"


def test_todo_motivo_declarado_e_ALCANCAVEL():
    """Mesmo guarda do leitor da rodada: motivo decorativo não vira métrica.

    Dois motivos declarados e nunca devolvidos foi defeito real neste projeto
    duas vezes (o `limite_pessimista`, e depois o `custo_nao_medido` e o
    `ordem_sem_prefixo_de_sombra` no leitor do 4.2).
    """
    from pathlib import Path

    fonte = (
        Path(__file__).resolve().parents[1]
        / "src" / "pulsearb" / "analysis" / "arbitragem.py"
    ).read_text(encoding="utf-8")
    corpo = fonte.split("MOTIVOS = {", 1)[1].split("\n}\n", 1)[1]

    nao_usados = [nome for nome in MOTIVOS if f'"{nome}"' not in corpo]

    assert not nao_usados, f"motivos declarados e nunca devolvidos: {nao_usados}"


# ── o veredito da varredura ──────────────────────────────────────────────
def _varredura():
    """O script, importado por caminho — `scripts/` não é pacote instalado."""
    import importlib.util
    import sys
    from pathlib import Path

    raiz = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "varredura_de_arbitragem", raiz / "scripts" / "varredura_de_arbitragem.py"
    )
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    sys.modules["varredura_de_arbitragem"] = modulo
    spec.loader.exec_module(modulo)
    return modulo


class TestOVereditoNaoFalaDoQueNaoOLHOU:
    """O defeito que a PRIMEIRA RODADA DE VERDADE expôs.

    Na VPS, em 2026-09-16, a varredura fez 599 passadas e imprimiu **❌ para a
    cesta neg-risk**. Mas as recusas diziam 11.980 `conjunto_incompleto`, que
    dividido por 599 passadas são **20 eventos** — de 22. Só DOIS chegaram à
    aritmética, e o script concluiu ❌ mesmo assim, três linhas depois de
    imprimir que recusa não é "não há arbitragem".

    É a distinção do `sem_recortes` no 1.6 — medida de ausência × ausência de
    medida — violada dentro do arquivo que a cita.
    """

    def test_maioria_recusada_da_NAO_AVALIAVEL_e_nao_reprova(self, capsys):
        v = _varredura()
        por_evento = {f"e{i}": "conjunto_com_fechado" for i in range(20)}
        por_evento.update({"e20": "avaliado", "e21": "avaliado"})

        v._imprimir_veredito(599, {}, {}, por_evento)
        saida = capsys.readouterr().out

        assert "NÃO AVALIÁVEL" in saida
        assert "2 de 22" in saida
        # O ❌ aparece na frase que explica o que isto NÃO é; o que não pode
        # existir é o VEREDITO ser ❌.
        assert "Veredito: ❌" not in saida

    def test_maioria_avaliada_e_sem_folga_da_REPROVA(self, capsys):
        """Quando o universo FOI olhado, ❌ é veredito legítimo — e barato."""
        v = _varredura()
        por_evento = {f"e{i}": "avaliado" for i in range(20)}
        por_evento["e20"] = "conjunto_com_fechado"

        v._imprimir_veredito(599, {}, {}, por_evento)
        saida = capsys.readouterr().out

        assert "Veredito: ❌" in saida
        assert "NÃO AVALIÁVEL" not in saida

    def test_a_contagem_por_EVENTO_aparece_sempre(self, capsys):
        """11.980 recusas escondiam que eram 20 eventos. O número que importa
        é quantos eventos existem e quantos foram olhados."""
        v = _varredura()

        v._imprimir_veredito(10, {}, {}, {"a": "avaliado", "b": "conjunto_sem_livro"})

        assert "1 avaliado(s) de 2" in capsys.readouterr().out


class TestARecusaDIZOQueFazer:
    """`conjunto_incompleto` não dizia o que destrava — fechado e sem-livro
    pedem coisas diferentes."""

    def test_fechado_que_resolveu_NAO_nao_mata_a_cesta(self):
        """O conserto que destrava os 20 eventos da primeira rodada.

        Um candidato eliminado é resultado fechado, e recusar o evento por
        causa dele jogou fora 20 dos 22 eventos sem medir nada. A identidade
        sobrevive: se todos os fechados resolveram NÃO, exatamente um dos
        ABERTOS vence e a cesta sobre eles paga 1,00.
        """
        v = _varredura()
        evento = {"markets": [
            {"closed": True, "outcomes": '["Yes","No"]',
             "outcomePrices": '["0","1"]'},
            {}, {},
        ]}

        assert v.conjunto_e_exaustivo(evento) == (True, "")
        assert len(v.mercados_da_cesta(evento)) == 2

    def test_fechado_que_resolveu_SIM_encerra_o_evento(self):
        """Aí os abertos valem ZERO, e a 'cesta' seria pagar por bilhete que
        já perdeu."""
        v = _varredura()
        evento = {"markets": [
            {"closed": True, "outcomes": '["Yes","No"]',
             "outcomePrices": '["1","0"]'},
            {}, {},
        ]}

        assert v.conjunto_e_exaustivo(evento) == (False, "evento_ja_decidido")

    def test_fechado_ILEGIVEL_recusa_em_vez_de_presumir(self):
        """Estado desconhecido é RECUSA, não 'provavelmente resolveu não'."""
        v = _varredura()
        evento = {"markets": [{"closed": True}, {}, {}]}

        assert v.conjunto_e_exaustivo(evento) == (
            False, "fechado_sem_resolucao_legivel"
        )

    def test_o_Yes_e_casado_pelo_NOME_e_nao_pela_posicao(self):
        """Presumir que o índice 0 é o "Yes" é o campo assumido a partir do
        que parecia razoável — o defeito do §6.1b e do §12.13."""
        v = _varredura()
        invertido = {"closed": True, "outcomes": '["No","Yes"]',
                     "outcomePrices": '["1","0"]'}

        assert v.resolveu_nao(invertido) is True

    def test_cesta_compra_so_os_ABERTOS(self):
        """Resultado que já resolveu NÃO custa zero e não se compra."""
        v = _varredura()
        evento = {"markets": [
            {"closed": True, "outcomes": '["Yes","No"]',
             "outcomePrices": '["0","1"]'},
            {"id": "a"}, {"id": "b"},
        ]}

        assert [m["id"] for m in v.mercados_da_cesta(evento)] == ["a", "b"]

    def test_resultado_sem_livro_tem_motivo_proprio(self):
        v = _varredura()
        evento = {"markets": [{"enableOrderBook": False}, {}]}

        assert v.conjunto_e_exaustivo(evento) == (False, "conjunto_sem_livro")

    def test_evento_de_um_resultado_so_tem_motivo_proprio(self):
        v = _varredura()

        assert v.conjunto_e_exaustivo({"markets": [{}]}) == (
            False, "conjunto_pequeno_demais"
        )

    def test_evento_todo_aberto_passa(self):
        v = _varredura()

        assert v.conjunto_e_exaustivo({"markets": [{}, {}]}) == (True, "")

    def test_todo_motivo_do_SCRIPT_e_alcancavel(self):
        """Mesmo guarda do núcleo — e foi ele que achou o `--cru` fantasma:
        a flag era citada numa mensagem e não existia no argparse."""
        from pathlib import Path

        v = _varredura()
        fonte = (
            Path(__file__).resolve().parents[1]
            / "scripts" / "varredura_de_arbitragem.py"
        ).read_text(encoding="utf-8")
        corpo = fonte.split("MOTIVOS = {", 1)[1].split("\n}\n", 1)[1]

        # Aspas simples TAMBÉM contam: os motivos usados dentro de f-string
        # saem como MOTIVOS['nome'], e procurar só aspas duplas acusaria
        # motivo vivo como decorativo.
        nao_usados = [
            n for n in v.MOTIVOS
            if f'"{n}"' not in corpo and f"'{n}'" not in corpo
        ]

        assert not nao_usados, f"motivos declarados e nunca devolvidos: {nao_usados}"

    def test_a_flag_cru_que_a_mensagem_CITA_existe_de_verdade(self):
        """Ela era citada e não existia — mandar rodar uma flag fantasma é
        pior que não sugerir nada."""
        v = _varredura()

        assert v.main.__doc__ is None or True  # a checagem real é o parse:
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.suppress(SystemExit):
            v.main(["--help"])

        assert "--cru" in buf.getvalue()


class TestBuscarAResolucaoQueOEventoNaoTROUXE:
    """O alvo que a segunda rodada na VPS apontou.

    2026-09-16, 599 passadas: **18 dos 22 eventos** caíram em
    `fechado_sem_resolucao_legivel`, e outros 2 resolveram limpo — a listagem
    `/events` traz `outcomes`/`outcomePrices` do mercado aninhado ÀS VEZES.
    Quando não traz, o dado está em `/markets/{id}` (§2), e pedir por ele é
    uma requisição, não uma suposição.
    """

    def _get_falso(self, respostas: dict, chamadas: list):
        async def get(url, params):
            chamadas.append(url)
            for sufixo, corpo in respostas.items():
                if url.endswith(sufixo):
                    return corpo
            raise RuntimeError(f"404 {url}")

        return get

    async def test_busca_o_mercado_e_o_evento_passa_a_ser_avaliavel(self):
        v = _varredura()
        evento = {"markets": [{"closed": True, "id": "777"}, {}, {}]}
        chamadas: list[str] = []
        get = self._get_falso(
            {"/markets/777": {"outcomes": '["Yes","No"]',
                              "outcomePrices": '["0","1"]'}},
            chamadas,
        )

        assert v.conjunto_e_exaustivo(evento)[1] == "fechado_sem_resolucao_legivel"
        await v.enriquecer_fechados(get, "https://g", evento, {})

        assert v.conjunto_e_exaustivo(evento) == (True, "")
        assert chamadas == ["https://g/markets/777"]

    async def test_a_resolucao_do_fechado_e_buscada_UMA_vez(self):
        """Fechado não reabre: a resolução dele não muda. Sem cache seriam 18
        buscas por passada × 599 passadas."""
        v = _varredura()
        chamadas: list[str] = []
        get = self._get_falso(
            {"/markets/777": {"outcomes": '["Yes","No"]',
                              "outcomePrices": '["0","1"]'}},
            chamadas,
        )
        cache: dict = {}

        for _ in range(5):
            evento = {"markets": [{"closed": True, "id": "777"}, {}, {}]}
            await v.enriquecer_fechados(get, "https://g", evento, cache)

        assert len(chamadas) == 1

    async def test_slug_em_vez_de_id_usa_a_outra_rota(self):
        """§2 documenta `/markets/{id}` E `/markets/slug/{slug}`."""
        v = _varredura()
        evento = {"markets": [{"closed": True, "slug": "quem-ganha"}, {}, {}]}
        chamadas: list[str] = []
        get = self._get_falso(
            {"/markets/slug/quem-ganha": {"outcomes": '["Yes","No"]',
                                          "outcomePrices": '["0","1"]'}},
            chamadas,
        )

        await v.enriquecer_fechados(get, "https://g", evento, {})

        assert chamadas == ["https://g/markets/slug/quem-ganha"]
        assert v.conjunto_e_exaustivo(evento) == (True, "")

    async def test_busca_que_FALHA_deixa_a_recusa_de_pe(self):
        """Não achar a resolução continua sendo estado desconhecido — recusa,
        nunca 'provavelmente resolveu não'."""
        v = _varredura()
        evento = {"markets": [{"closed": True, "id": "999"}, {}, {}]}

        await v.enriquecer_fechados(get=self._get_falso({}, []), base_gamma="https://g",
                                    evento=evento, cache={})

        assert v.conjunto_e_exaustivo(evento) == (
            False, "fechado_sem_resolucao_legivel"
        )

    async def test_nao_busca_o_que_o_evento_JA_trouxe(self):
        """Os 2 eventos que resolveram limpo não podem virar requisição."""
        v = _varredura()
        evento = {"markets": [
            {"closed": True, "id": "1", "outcomes": '["Yes","No"]',
             "outcomePrices": '["0","1"]'},
            {}, {},
        ]}
        chamadas: list[str] = []

        await v.enriquecer_fechados(
            self._get_falso({}, chamadas), "https://g", evento, {}
        )

        assert chamadas == []

    async def test_mercado_ABERTO_nunca_e_buscado(self):
        """Só o fechado tem resolução; buscar aberto seria requisição à toa em
        cima de 22 eventos."""
        v = _varredura()
        evento = {"markets": [{"id": "1"}, {"id": "2"}]}
        chamadas: list[str] = []

        await v.enriquecer_fechados(
            self._get_falso({}, chamadas), "https://g", evento, {}
        )

        assert chamadas == []


class TestODiagnosticoDoFechadoIlegivel:
    """O `--cru` tem de responder as QUATRO hipóteses numa rodada só.

    O conserto de `enriquecer_fechados` não destravou os 18 eventos, e a saída
    da varredura não distingue: a busca falhou, o mercado cheio também não traz
    o par, não há identificador, ou o nome do resultado não bate com "Yes".
    Escolher uma e pedir outra rodada de 10 min seria adivinhar.
    """

    async def test_sem_identificador_o_diagnostico_DIZ_isso(self, capsys):
        v = _varredura()

        async def get(url, params):
            raise AssertionError("não devia buscar sem identificador")

        await v._diagnosticar_fechado(get, "https://g", {"closed": True})

        assert "SEM IDENTIFICADOR" in capsys.readouterr().out

    async def test_busca_que_falha_e_NOMEADA(self, capsys):
        v = _varredura()

        async def get(url, params):
            raise RuntimeError("404")

        await v._diagnosticar_fechado(get, "https://g", {"closed": True, "id": "7"})
        saida = capsys.readouterr().out

        assert "A BUSCA FALHOU" in saida
        assert "https://g/markets/7" in saida

    async def test_par_presente_com_nome_que_NAO_bate_e_apontado(self, capsys):
        """A hipótese mais provável, e a que o casamento por nome não cobre."""
        v = _varredura()

        async def get(url, params):
            return {"outcomes": '["Trump","Biden"]', "outcomePrices": '["0","1"]'}

        await v._diagnosticar_fechado(get, "https://g", {"closed": True, "id": "7"})

        assert "não bate com 'yes'/'sim'" in capsys.readouterr().out

    async def test_diz_se_foi_closed_ou_active_false(self, capsys):
        """As duas não são a mesma coisa: inativo pode nunca ter aberto, e aí
        não existe preço final para ler nem vai existir."""
        v = _varredura()

        async def get(url, params):
            return {}

        await v._diagnosticar_fechado(
            get, "https://g", {"active": False, "id": "7"}
        )
        saida = capsys.readouterr().out

        assert "closed=None" in saida
        assert "active=False" in saida
