"""Endurecimento do laço maker (auditoria de 2026-09-26, segunda etapa).

Cada classe prende um achado, com o teste que FALHAVA antes da correção
quando o achado é de comportamento:

- F2 — o estado RECONCILIAR nunca era reconciliado durante a rodada: a cotação
       ficava em `abertas`, a caixa creditava reward por ela e o `decidir`
       podia MANTÊ-LA como se repousasse.
- F4 — sem livro, o portão não reavaliava a exposição que já existia: com a
       chave puxada ou o disjuntor armado, a cotação ficava no livro.
- N1 — os motivos do laço eram literais soltos.
- N2 — `efeito.resultado`/`efeito.motivo` não eram contados.
- N3 — o relato não separava "repousando" de "estado desconhecido".
- N4 — ninguém cronometrava a passada do maker.
- T4 — nada prendia a propriedade de ESCRITOR ÚNICO do laço.
- T5 — nada ia do `LivrosAoVivo` mudo até o `passo` que não cota.
- S1 — o cliente sombra escrevia a prova de ausência como literal.

Os símbolos NOVOS são importados dentro dos testes que os usam, para os
testes de COMPORTAMENTO falharem por asserção — e não por `ImportError` —
contra o código antigo.
"""

from __future__ import annotations

import ast
import asyncio
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from pulsearb.backtest.book import OrderBook
from pulsearb.execution.cliente import (
    ErroDeLeitura,
    EstadoDoCancelamento,
    EstadoDoEnvio,
    OrdemAberta,
    ResultadoDoCancelamento,
    ResultadoDoEnvio,
)
from pulsearb.execution.cliente_sombra import ClienteSombraDeOrdens
from pulsearb.live.execucao_maker import ResultadoDaAcao
from pulsearb.live.laco_maker import LacoMaker
from pulsearb.live.rastreador import JanelaAoVivo
from pulsearb.settings import FeedSettings, Mode, RiskSettings, Settings

RAIZ = Path(__file__).resolve().parent.parent
FONTE_DO_LACO = RAIZ / "src" / "pulsearb" / "live" / "laco_maker.py"
FONTE_DO_SHADOW = RAIZ / "src" / "pulsearb" / "live" / "shadow.py"
FONTE_DO_SOMBRA = RAIZ / "src" / "pulsearb" / "execution" / "cliente_sombra.py"

SLUG = "btc-updown-4h-1"


# ─────────────────────────────────────────────────────────────── dublês
def _janela(slug=SLUG, fechamento=10_000.0):
    return JanelaAoVivo(
        slug=slug,
        asset="btc",
        jogo="twap",
        condition_id="cond-1",
        token_up="tok-up",
        token_down="tok-down",
        duracao_s=14_400,
        abertura_epoch=fechamento - 14_400,
        fechamento_epoch=fechamento,
        tick_size=0.01,
        min_order_size=5.0,
        fee_rate=0.0,
        fee_exponent=1.0,
        reward_daily_rate=100.0,
        reward_min_size=5.0,
        reward_max_spread=0.03,
    )


def _livro(mid=0.50):
    return OrderBook(
        asset_id="tok-up",
        bids=[(mid - 0.01, 500.0), (mid - 0.02, 500.0)],
        asks=[(mid + 0.01, 500.0), (mid + 0.02, 500.0)],
    )


def _livro_de(livro):
    def obter(token_id, *, agora_ns):
        return livro

    return obter


class _PortaoDuble:
    """Só o método que o laço usa. `decisao` pode ser trocada no meio."""

    def __init__(self, pode=True, motivo=None):
        self.decisao = SimpleNamespace(pode=pode, motivo=motivo)
        self.consultas: list = []

    def avaliar_risco(self, ordem, *, feeds_saudaveis, melhor_bid, melhor_ask):
        self.consultas.append((ordem, melhor_bid, melhor_ask))
        return self.decisao


#: No roteiro de `_ClienteDeRoteiro`: o `enviar` levanta (ver a classe).
LEVANTA = "levanta"


class _ClienteDeRoteiro:
    """Um servidor de mentira que obedece a um roteiro.

    `envios`: um `(EstadoDoEnvio, entrou)` por chamada de `enviar` — `entrou`
    diz se a ordem foi parar no livro do servidor (o INCERTA que afinal
    entrou). No lugar do estado, `LEVANTA` faz o `enviar` levantar DEPOIS de
    a ordem (se `entrou`) ter ido ao livro. Esgotado o roteiro, aceita. `cancelamentos`: um
    `EstadoDoCancelamento` por chamada; esgotado, cancela. `no_servidor` é o
    que `listar_ordens_abertas` devolve (filtrado por token, como o real), e
    `leitura_falha` a faz levantar `ErroDeLeitura`.
    """

    def __init__(self, envios=(), cancelamentos=()):
        self.envios = list(envios)
        self.cancelamentos = list(cancelamentos)
        self.enviados: list = []
        self.cancelados: list[str] = []
        self.leituras: list[str | None] = []
        self.no_servidor: list[OrdemAberta] = []
        self.leitura_falha = False

    async def enviar(self, ordem, *, janela):
        self.enviados.append(ordem)
        n = len(self.enviados)
        estado, entrou = (
            self.envios.pop(0) if self.envios else (EstadoDoEnvio.ACEITA, True)
        )
        if entrou:
            self.no_servidor.append(
                OrdemAberta(
                    id=f"srv-{n}",
                    token_id=ordem.token_id,
                    side="BUY",
                    price=ordem.preco_limite,
                    original_size=ordem.shares,
                    size_matched=0.0,
                    status="LIVE",
                )
            )
        if estado == LEVANTA:
            raise RuntimeError("transporte levantou fora de ErroDeTransporte")
        if estado is EstadoDoEnvio.ACEITA:
            return ResultadoDoEnvio(estado, order_id=f"srv-{n}", id_do_cliente=f"cli-{n}")
        return ResultadoDoEnvio(estado, id_do_cliente=f"cli-{n}")

    async def cancelar(self, order_id):
        self.cancelados.append(order_id)
        estado = (
            self.cancelamentos.pop(0)
            if self.cancelamentos
            else EstadoDoCancelamento.CANCELADA
        )
        if estado is EstadoDoCancelamento.CANCELADA:
            self.no_servidor = [o for o in self.no_servidor if o.id != order_id]
        return ResultadoDoCancelamento(estado, order_id=order_id)

    async def listar_ordens_abertas(self, *, token_id=None, market=None):
        self.leituras.append(token_id)
        if self.leitura_falha:
            raise ErroDeLeitura("timeout no GET /data/orders")
        return [o for o in self.no_servidor if token_id is None or o.token_id == token_id]


def _laco(cliente, portao=None):
    return LacoMaker(
        cliente=cliente,
        tamanho_da_cotacao=50.0,
        portao=portao if portao is not None else _PortaoDuble(),
    )


async def _passo(laco, t, *, janelas=None, livro=...):
    return await laco.passo(
        [_janela()] if janelas is None else janelas,
        livro_de=_livro_de(_livro() if livro is ... else livro),
        agora_epoch=t,
        agora_ns=int(t * 1e9),
    )


async def _envio_incerto(entrou=False):
    """Passada 1: o POST do Up não teve resposta. Devolve (laço, cliente)."""
    cliente = _ClienteDeRoteiro(envios=[(EstadoDoEnvio.INCERTA, entrou)])
    laco = _laco(cliente)
    efeitos = await _passo(laco, 1000.0)
    assert [e.resultado for e in efeitos] == [ResultadoDaAcao.RECONCILIAR]
    assert len(cliente.enviados) == 1
    return laco, cliente


# ─────────────────────────────────────────────────────────────────── F2
class TestEstadoDesconhecidoNaRodada:
    """F2 (ALTA): RECONCILIAR só era lido no arranque. Na passada seguinte a
    cotação desconhecida era tratada como normal — a caixa creditava reward
    por uma ordem que talvez nem existisse, e o `decidir` podia MANTÊ-LA."""

    async def test_passada_seguinte_le_o_servidor_marca_conta_e_NAO_acerta_a_caixa(self):
        laco, cliente = await _envio_incerto()
        cliente.leitura_falha = True

        await _passo(laco, 1100.0)

        assert cliente.leituras, "a passada seguinte tinha de perguntar ao servidor"
        assert laco.motivos.get("estado_desconhecido") == 1
        assert laco.resumo().get("cotacoes_em_estado_desconhecido") == 1
        assert laco.caixa.acertos == 0, "reward creditado por cotação desconhecida"
        assert laco.abertas == {}, "desconhecida não é 'repousando'"
        assert len(cliente.enviados) == 1

    async def test_leitura_que_falha_mantem_degradado_e_conta_a_cada_passada(self):
        laco, cliente = await _envio_incerto()
        cliente.leitura_falha = True

        await _passo(laco, 1100.0)
        await _passo(laco, 1200.0)

        assert laco.motivos.get("estado_desconhecido") == 2
        rodada = laco.resumo()["reconciliacao_na_rodada"]
        assert rodada["tentativas"] == 2
        assert rodada["falhas_de_leitura"] == 2
        assert rodada["resolvidas"] == 0
        assert laco.resumo()["cotacoes_em_estado_desconhecido"] == 1
        assert len(cliente.enviados) == 1, "não se cota por cima do desconhecido"

    async def test_livro_vazio_depois_de_envio_INCERTA_bloqueia_a_janela_e_NAO_recota(
        self,
    ):
        """Revisão do #203 (P1/P2): ausência nas ordens abertas prova que nada
        REPOUSA, não que nada EXECUTOU — um INCERTA aceito e preenchido também
        some da lista. A janela sai do desconhecido mas NÃO volta a cotar
        (e não esbarra na reserva do `id_do_cliente` que o cliente real
        guarda como INCERTA)."""
        laco, cliente = await _envio_incerto(entrou=False)

        await _passo(laco, 1100.0)

        assert cliente.leituras == ["tok-up", "tok-down"]
        assert laco.resumo()["cotacoes_em_estado_desconhecido"] == 0
        rodada = laco.resumo()["reconciliacao_na_rodada"]
        assert rodada["resolvidas"] == 0
        assert rodada["bloqueadas_sem_prova_de_execucao"] == 1
        assert laco.resumo()["janelas_sem_prova_de_execucao"] == 1
        assert laco.motivos.get("sem_prova_de_execucao") == 1
        assert SLUG not in laco.abertas
        assert len(cliente.enviados) == 1, "recotou sem prova de que nada executou"

        # Passadas seguintes: continua bloqueada, sem ler de novo nem cotar.
        leituras_antes = len(cliente.leituras)
        await _passo(laco, 1200.0)
        assert len(cliente.enviados) == 1
        assert len(cliente.leituras) == leituras_antes
        assert laco.motivos.get("sem_prova_de_execucao") == 2

    async def test_T2_passada_seguinte_a_envio_incerto_NAO_reenvia_e_cancela_a_orfa(self):
        """O INCERTA que afinal ENTROU: a leitura acha a ordem como órfã, ela
        é cancelada pelo caminho de sempre, e nada novo sai até uma leitura
        seguinte não achar nada."""
        laco, cliente = await _envio_incerto(entrou=True)

        await _passo(laco, 1100.0)

        assert len(cliente.enviados) == 1, "reenviou por cima de um INCERTA"
        assert cliente.cancelados == ["srv-1"]
        rodada = laco.resumo()["reconciliacao_na_rodada"]
        assert rodada["orfas_achadas"] == 1
        assert rodada["orfas_canceladas"] == 1
        assert laco.resumo()["cotacoes_em_estado_desconhecido"] == 1

        # A leitura seguinte não acha nada: a órfã saiu, mas nada prova que
        # ela não executou antes do cancelamento — a janela fica bloqueada,
        # sem recotar (revisão do #203).
        await _passo(laco, 1200.0)
        assert laco.resumo()["cotacoes_em_estado_desconhecido"] == 0
        rodada = laco.resumo()["reconciliacao_na_rodada"]
        assert rodada["resolvidas"] == 0
        assert rodada["bloqueadas_sem_prova_de_execucao"] == 1
        assert len(cliente.enviados) == 1

    async def test_orfa_sem_id_prende_a_janela_no_desconhecido(self):
        laco, cliente = await _envio_incerto()
        cliente.no_servidor.append(
            OrdemAberta(
                id="", token_id="tok-up", side="BUY", price=0.49,
                original_size=50.0, size_matched=None, status="LIVE",
            )
        )

        await _passo(laco, 1100.0)
        await _passo(laco, 1200.0)

        assert cliente.cancelados == []
        assert laco.resumo()["reconciliacao_na_rodada"]["orfas_sem_id"] == 2
        assert laco.resumo()["cotacoes_em_estado_desconhecido"] == 1

    async def test_perna_Down_incerta_cancela_o_Up_que_o_servidor_confirma(self):
        """Up aceito, Down INCERTA que não entrou: a leitura casa o Up, e ele
        sai pelo `aplicar_decisao`; só a prova do cancelamento resolve."""
        cliente = _ClienteDeRoteiro(
            envios=[(EstadoDoEnvio.ACEITA, True), (EstadoDoEnvio.INCERTA, False)]
        )
        laco = _laco(cliente)
        efeitos = await _passo(laco, 1000.0)
        assert efeitos[0].motivo == "envio_incerto"

        await _passo(laco, 1100.0)

        assert cliente.cancelados[0] == "srv-1"
        rodada = laco.resumo()["reconciliacao_na_rodada"]
        assert rodada["cotacoes_conhecidas_canceladas"] == 1
        assert rodada["resolvidas"] == 1

    async def test_cancelamento_incerto_de_janela_que_FECHOU_ainda_e_reconciliado(self):
        """A janela fechada não passa mais pelo `_passo_da_janela`, e a ordem
        dela pode repousar do mesmo jeito: a reconciliação não depende dela
        estar aberta."""
        cliente = _ClienteDeRoteiro(cancelamentos=[EstadoDoCancelamento.INCERTA])
        laco = _laco(cliente)
        await _passo(laco, 1000.0)
        assert len(cliente.no_servidor) == 2

        efeitos = await _passo(laco, 1100.0, janelas=[])
        assert efeitos[0].motivo == "cancelamento_incerto"
        assert laco.resumo()["cotacoes_em_estado_desconhecido"] == 1

        await _passo(laco, 1200.0, janelas=[])

        assert cliente.no_servidor == [], "as duas pernas tinham de sair com prova"
        assert laco.resumo()["cotacoes_em_estado_desconhecido"] == 0

    @staticmethod
    def _contar_decidir(monkeypatch):
        """Conta as chamadas do `decidir` do laço: MANTER é uma decisão, e a
        janela desconhecida não pode ter decisão nenhuma."""
        import pulsearb.live.laco_maker as modulo

        chamadas: list = []
        original = modulo.decidir

        def contando(*args, **kwargs):
            chamadas.append(args)
            return original(*args, **kwargs)

        monkeypatch.setattr(modulo, "decidir", contando)
        return chamadas

    async def test_excecao_na_segunda_perna_SEM_Down_no_livro_cancela_o_Up(
        self, monkeypatch
    ):
        """Revisão do Codex no #203, caso (a): algo levanta na perna Down
        depois de o Up ser ACEITO e o Down NÃO foi ao livro. A passada
        seguinte não decide (nem MANTER), lê os dois tokens, acha o Up
        casado e o cancela pelo `aplicar_decisao`; só a PROVA do cancelamento
        tira a janela do desconhecido."""
        cliente = _ClienteDeRoteiro(
            envios=[(EstadoDoEnvio.ACEITA, True), (LEVANTA, False)],
            cancelamentos=[EstadoDoCancelamento.INCERTA],
        )
        laco = _laco(cliente)
        efeitos = await _passo(laco, 1000.0)
        assert [e.motivo for e in efeitos] == ["excecao_na_segunda_perna"]
        decisoes = self._contar_decidir(monkeypatch)

        # 1ª leitura: o Up casa, mas o cancelamento dele fica INCERTO — não
        # é prova, a janela segue desconhecida.
        await _passo(laco, 1100.0)
        assert decisoes == [], "decidiu (MANTER?) sobre estado desconhecido"
        assert cliente.leituras == ["tok-up", "tok-down"]
        assert cliente.cancelados == ["srv-1"]
        assert laco.resumo()["cotacoes_em_estado_desconhecido"] == 1

        # 2ª leitura: o Up ainda casa, e agora sai com prova. SÓ ENTÃO a
        # janela volta ao normal — e só então há decisão (a cotação nova).
        await _passo(laco, 1200.0)
        assert cliente.cancelados == ["srv-1", "srv-1"]
        assert "srv-1" not in {o.id for o in cliente.no_servidor}
        assert laco.resumo()["cotacoes_em_estado_desconhecido"] == 0
        assert laco.resumo()["reconciliacao_na_rodada"]["cotacoes_conhecidas_canceladas"] == 1
        assert len(decisoes) == 1 and SLUG in laco.abertas

    async def test_excecao_na_segunda_perna_COM_Down_orfao_cancela_os_dois(
        self, monkeypatch
    ):
        """Caso (b): a exceção veio DEPOIS de o Down ir ao livro — há um Down
        que ninguém registrou. A leitura do token Down o acha como órfão e o
        cancela pelo `cancelar_orfas`; a janela só sai do desconhecido na
        leitura seguinte, que não acha órfã e cancela o Up casado."""
        cliente = _ClienteDeRoteiro(
            envios=[(EstadoDoEnvio.ACEITA, True), (LEVANTA, True)]
        )
        laco = _laco(cliente)
        await _passo(laco, 1000.0)
        down_orfao = next(o for o in cliente.no_servidor if o.token_id == "tok-down")
        assert laco.abertas == {}
        decisoes = self._contar_decidir(monkeypatch)

        await _passo(laco, 1100.0)
        assert decisoes == []
        assert cliente.cancelados == [down_orfao.id], "o Down órfão tinha de sair"
        rodada = laco.resumo()["reconciliacao_na_rodada"]
        assert rodada["orfas_achadas"] == 1 and rodada["orfas_canceladas"] == 1
        assert laco.resumo()["cotacoes_em_estado_desconhecido"] == 1

        await _passo(laco, 1200.0)
        assert cliente.cancelados == [down_orfao.id, "srv-1"]
        vivas = {o.id for o in cliente.no_servidor}
        assert "srv-1" not in vivas and down_orfao.id not in vivas
        assert laco.resumo()["cotacoes_em_estado_desconhecido"] == 0
        assert len(decisoes) == 1, "a decisão só volta depois do estado provado"

    async def test_desconhecido_nao_passa_pelo_recolher(self):
        """Tudo que lê `abertas` entre passadas tratava o registro desconhecido
        como cotação repousando: o recolher o "tirava do livro" com um lado de
        bids vazio — que é o gatilho mais forte dele."""
        laco, _ = await _envio_incerto()
        laco.recolhe_quando_o_livro_anda = True
        sem_bids = OrderBook(asset_id="tok-up", bids=[], asks=[(0.51, 500.0)])

        efeitos = await laco.recolher_se_o_livro_andou(_livro_de(sem_bids), agora_ns=1)

        assert efeitos == []
        assert "livro_andou_contra" not in laco.motivos


# ─────────────────────────────────────────────────────────────────── F4
class TestPortaoSemLivro:
    """F4 (MÉDIA): livro faltando retornava ANTES do portão, e o 4.0 registra
    que o portão vale para a exposição que já existe."""

    async def _repousando(self, portao, tmp_path):
        cliente = ClienteSombraDeOrdens(
            caminho_do_diario=tmp_path / "d.jsonl", modo=Mode.SHADOW
        )
        laco = _laco(cliente, portao)
        await _passo(laco, 1000.0)
        assert SLUG in laco.abertas and len(cliente.repousadas) == 2
        return laco, cliente

    async def test_kill_com_livro_indisponivel_tira_a_cotacao(self, tmp_path):
        portao = _PortaoDuble()
        laco, cliente = await self._repousando(portao, tmp_path)
        portao.decisao = SimpleNamespace(pode=False, motivo="kill_acionado")

        await _passo(laco, 1100.0, livro=None)

        assert laco.abertas == {}
        assert cliente.repousadas == {}, "o cancelamento tinha de sair"
        assert laco.motivos.get("portao:kill_acionado") == 1
        assert laco.motivos.get("livro_indisponivel") == 1
        # Perguntou SEM topo de livro e no preço guardado.
        _, bid, ask = portao.consultas[-1]
        assert (bid, ask) == (None, None)

    async def test_disjuntor_com_livro_sem_meio_tira_a_cotacao(self, tmp_path):
        portao = _PortaoDuble()
        laco, _ = await self._repousando(portao, tmp_path)
        portao.decisao = SimpleNamespace(pode=False, motivo="disjuntor_armado")
        so_bids = OrderBook(asset_id="tok-up", bids=[(0.49, 500.0)], asks=[])

        await _passo(laco, 1100.0, livro=so_bids)

        assert laco.abertas == {}
        assert laco.motivos.get("livro_sem_meio") == 1
        assert laco.motivos.get("portao:disjuntor_armado") == 1

    async def test_so_livro_desconhecido_NAO_cancela(self, tmp_path):
        """A decisão de desenho de `_dados_da_passada` segue: falta de dado
        NOSSO não tira a cotação — só o portão de SISTEMA tira."""
        portao = _PortaoDuble()
        laco, cliente = await self._repousando(portao, tmp_path)
        portao.decisao = SimpleNamespace(pode=False, motivo="livro_desconhecido")

        await _passo(laco, 1100.0, livro=None)

        assert SLUG in laco.abertas
        assert len(cliente.repousadas) == 2
        assert not any(k.startswith("portao:") for k in laco.motivos)

    async def test_com_o_portao_REAL_a_chave_puxada_tira_e_sem_ela_fica(self, tmp_path):
        from pulsearb.risk import PortaoDeRisco

        kill = tmp_path / "KILL"
        real = PortaoDeRisco(
            RiskSettings(),
            Mode.SHADOW,
            caminho_do_registro=tmp_path / "registro.json",
            caminho_do_kill=kill,
            hoje="2026-09-26",
        )
        laco, cliente = await self._repousando(_PortaoDuble(), tmp_path)
        laco.portao = real

        await _passo(laco, 1100.0, livro=None)
        assert SLUG in laco.abertas, "sem chave, o real diz livro_desconhecido"

        kill.write_text("parar", encoding="utf-8")
        await _passo(laco, 1200.0, livro=None)
        assert laco.abertas == {}
        assert cliente.repousadas == {}
        assert laco.motivos.get("portao:kill_acionado") == 1

    def test_os_motivos_de_sistema_sao_do_gates_e_nao_incluem_o_de_livro(self):
        from pulsearb.live.laco_maker import MOTIVOS_DE_SISTEMA_DO_PORTAO
        from pulsearb.risk import MOTIVOS

        assert MOTIVOS_DE_SISTEMA_DO_PORTAO <= MOTIVOS.TODOS
        assert MOTIVOS.LIVRO_DESCONHECIDO not in MOTIVOS_DE_SISTEMA_DO_PORTAO
        assert MOTIVOS.KILL_ACIONADO in MOTIVOS_DE_SISTEMA_DO_PORTAO
        assert MOTIVOS.DISJUNTOR_ARMADO in MOTIVOS_DE_SISTEMA_DO_PORTAO


# ─────────────────────────────────────────────────────────────────── N1
def _chamadas(arvore, nomes):
    """(chamada, nome) para cada `self.<nome>(...)` da árvore."""
    for no in ast.walk(arvore):
        if (
            isinstance(no, ast.Call)
            and isinstance(no.func, ast.Attribute)
            and isinstance(no.func.value, ast.Name)
            and no.func.value.id == "self"
            and no.func.attr in nomes
        ):
            yield no, no.func.attr


def _corpo_da_classe(arvore, nome):
    return next(
        no for no in ast.walk(arvore) if isinstance(no, ast.ClassDef) and no.name == nome
    )


class TestMotivosDoLaco:
    def test_a_enumeracao_esta_completa_e_os_valores_nao_mudaram(self):
        from pulsearb.live.laco_maker import MOTIVOS_DO_LACO

        constantes = {
            v for k, v in vars(MOTIVOS_DO_LACO).items()
            if k.isupper() and isinstance(v, str)
        }
        assert constantes == set(MOTIVOS_DO_LACO.TODOS)
        assert MOTIVOS_DO_LACO.DO_PORTAO <= MOTIVOS_DO_LACO.TODOS
        # Os valores são contrato: o leitor da rodada e o quadro os citam.
        assert {
            "livro_indisponivel", "livro_sem_meio", "sem_pool_de_reward",
            "pool_sumiu", "sem_microprice", "fracao_do_pool_acima_do_teto",
            "perna_down_sem_bids", "sem_espaco_para_recuar", "sem_portao",
            "recusado_sem_motivo", "janela_fechou", "livro_andou_contra",
            "pausa_por_fill_toxico", "janela_sem_tempo", "estado_desconhecido",
            "sem_prova_de_execucao",
        } == constantes

    def test_nenhum_motivo_do_laco_aparece_como_literal_fora_da_enumeracao(self):
        from pulsearb.live.laco_maker import MOTIVOS_DO_LACO

        arvore = ast.parse(FONTE_DO_LACO.read_text(encoding="utf-8"))
        classe = _corpo_da_classe(arvore, "MOTIVOS_DO_LACO")
        dentro = {id(no) for no in ast.walk(classe)}
        soltos = [
            (no.lineno, no.value)
            for no in ast.walk(arvore)
            if isinstance(no, ast.Constant)
            and no.value in MOTIVOS_DO_LACO.TODOS
            and id(no) not in dentro
        ]
        assert not soltos, soltos

    def test_quem_conta_ou_sai_so_recebe_constante_ou_repasse_conhecido(self):
        """Inspeção da FONTE, no molde do `test_nenhum_Efeito_da_fonte_usa_
        motivo_literal`: `_contar`, `_recusar`, `_sair` e `_recolher` recebem
        `MOTIVOS_DO_LACO.X`, `PREFIXO_DO_PORTAO + ...`, ou REPASSAM um motivo
        que tem dono conhecido."""
        from pulsearb.live.laco_maker import MOTIVOS_DO_LACO

        arvore = ast.parse(FONTE_DO_LACO.read_text(encoding="utf-8"))
        repasses = {
            "motivo",  # parâmetro de `_sair`/`_recusar`/`_recolher`
            "recusa",  # de `_dados_da_passada`, conferido abaixo
            "decisao.motivo",  # `repouso.MOTIVOS`
        }
        problemas = []
        referenciadas = set()

        def conferir(no, linha):
            if (
                isinstance(no, ast.Attribute)
                and isinstance(no.value, ast.Name)
                and no.value.id == "MOTIVOS_DO_LACO"
            ):
                referenciadas.add(no.attr)
                if no.attr not in vars(MOTIVOS_DO_LACO):
                    problemas.append(f"linha {linha}: {no.attr} inexistente")
                return
            if (
                isinstance(no, ast.BinOp)
                and isinstance(no.left, ast.Name)
                and no.left.id == "PREFIXO_DO_PORTAO"
            ):
                return
            if ast.unparse(no) not in repasses:
                problemas.append(f"linha {linha}: {ast.unparse(no)!r}")

        for chamada, nome in _chamadas(arvore, {"_contar", "_recusar", "_sair", "_recolher"}):
            motivo = next((k.value for k in chamada.keywords if k.arg == "motivo"), None)
            if motivo is None:
                posicao = 0 if nome == "_contar" else 1
                if len(chamada.args) <= posicao:
                    continue
                motivo = chamada.args[posicao]
            conferir(motivo, chamada.lineno)

        # O que `_dados_da_passada` devolve como recusa também é constante.
        dados = next(
            no for no in ast.walk(arvore)
            if isinstance(no, ast.FunctionDef) and no.name == "_dados_da_passada"
        )
        for ret in (n for n in ast.walk(dados) if isinstance(n, ast.Return)):
            if isinstance(ret.value, ast.Tuple) and len(ret.value.elts) == 2:
                segundo = ret.value.elts[1]
                if not (isinstance(segundo, ast.Constant) and segundo.value is None):
                    conferir(segundo, ret.lineno)

        assert not problemas, problemas

    def test_toda_entrada_declarada_e_alcancavel_na_fonte(self):
        from pulsearb.live.laco_maker import MOTIVOS_DO_LACO

        arvore = ast.parse(FONTE_DO_LACO.read_text(encoding="utf-8"))
        classe = _corpo_da_classe(arvore, "MOTIVOS_DO_LACO")
        dentro = {id(no) for no in ast.walk(classe)}
        usadas = {
            no.attr
            for no in ast.walk(arvore)
            if isinstance(no, ast.Attribute)
            and isinstance(no.value, ast.Name)
            and no.value.id == "MOTIVOS_DO_LACO"
            and id(no) not in dentro
        }
        declaradas = {
            k for k, v in vars(MOTIVOS_DO_LACO).items()
            if k.isupper() and isinstance(v, str)
        }
        assert declaradas <= usadas, declaradas - usadas

    async def test_toda_chave_de_motivos_pertence_as_enumeracoes(self):
        """Em execução: um punhado de passadas por caminhos diferentes, e toda
        chave de `motivos` tem dono — laço, repouso, ou portão com prefixo.
        E `motivos_do_efeito` só tem `MOTIVOS_DO_EFEITO`."""
        from pulsearb.live import repouso
        from pulsearb.live.execucao_maker import MOTIVOS_DO_EFEITO
        from pulsearb.live.laco_maker import MOTIVOS_DO_LACO, PREFIXO_DO_PORTAO
        from pulsearb.risk import MOTIVOS

        permitidas = (
            (MOTIVOS_DO_LACO.TODOS - MOTIVOS_DO_LACO.DO_PORTAO)
            | set(repouso.MOTIVOS)
            | {PREFIXO_DO_PORTAO + m for m in MOTIVOS.TODOS | MOTIVOS_DO_LACO.DO_PORTAO}
        )

        portao = _PortaoDuble()
        cliente = _ClienteDeRoteiro(envios=[(EstadoDoEnvio.INCERTA, False)])
        laco = _laco(cliente, portao)
        await _passo(laco, 1000.0)                        # envio_incerto
        await _passo(laco, 1100.0)                        # resolve e cota
        await _passo(laco, 1110.0)                        # histerese
        await _passo(laco, 1200.0, livro=None)            # livro_indisponivel
        portao.decisao = SimpleNamespace(pode=False, motivo="feed_parado")
        await _passo(laco, 1300.0)                        # portao:feed_parado
        laco.portao = None
        await _passo(laco, 1400.0)                        # portao:sem_portao
        await _passo(laco, 1500.0, janelas=[])            # nada a fechar
        sem_pool = replace(_janela(), reward_daily_rate=None)
        await _passo(laco, 1600.0, janelas=[sem_pool])    # sem_pool_de_reward

        assert laco.motivos, "o cenário não produziu motivo nenhum"
        assert set(laco.motivos) <= permitidas, set(laco.motivos) - permitidas
        assert set(laco.motivos_do_efeito) <= MOTIVOS_DO_EFEITO.TODOS


# ─────────────────────────────────────────────────────────────────── N2/N3
class TestTelemetriaDoEfeito:
    async def test_efeitos_por_resultado_e_motivo_quando_a_execucao_diverge(self):
        laco, _ = await _envio_incerto()

        resumo = laco.resumo()
        assert resumo["efeitos"] == {"reconciliar": 1}
        assert resumo["motivos_do_efeito"] == {"envio_incerto": 1}
        # À parte de `motivos`: o leitor da rodada lê aquele como está.
        assert "envio_incerto" not in resumo["motivos"]

    async def test_sucesso_conta_o_resultado_e_nao_o_motivo(self):
        laco = _laco(_ClienteDeRoteiro())
        await _passo(laco, 1000.0)

        resumo = laco.resumo()
        assert resumo["efeitos"] == {"colocada": 1}
        assert resumo["motivos_do_efeito"] == {}

    async def test_o_resumo_publica_os_blocos_novos_com_chaves_estaveis(self):
        laco = _laco(_ClienteDeRoteiro())
        resumo = laco.resumo()

        assert resumo["cotacoes_em_estado_desconhecido"] == 0
        assert resumo["janelas_sem_prova_de_execucao"] == 0
        assert set(resumo["reconciliacao_na_rodada"]) == {
            "tentativas", "resolvidas", "falhas_de_leitura", "orfas_achadas",
            "orfas_canceladas", "orfas_sem_id", "cotacoes_conhecidas_canceladas",
            "bloqueadas_sem_prova_de_execucao", "nota",
        }

    async def test_arranque_expoe_orfas_sem_id(self):
        cliente = _ClienteDeRoteiro()
        cliente.no_servidor.append(
            OrdemAberta(
                id="", token_id="tok-x", side="BUY", price=0.5,
                original_size=5.0, size_matched=None, status="LIVE",
            )
        )
        laco = _laco(cliente)

        await laco.reconciliar_no_arranque()

        assert laco.ultima_reconciliacao["orfas_sem_id"] == 1
        assert laco.ultima_reconciliacao["cancelamentos"] == {}


# ─────────────────────────────────────────────────────── processo (N4/T4/T5)
def _settings(tmp_path):
    return Settings(
        mode=Mode.SHADOW,
        feeds=FeedSettings(),
        risk=RiskSettings(
            caminho_do_registro=str(tmp_path / "registro.json"),
            caminho_do_kill=str(tmp_path / "KILL"),
        ),
    )


def _processo(tmp_path, *, livros=None):
    from pulsearb.live.shadow import ProcessoShadow

    ciclo = SimpleNamespace(
        motor=SimpleNamespace(
            rastreador=SimpleNamespace(abertas=lambda **_: []),
            livros=livros or SimpleNamespace(negocios_desde=lambda *_a, **_k: []),
        ),
        feeds_saudaveis=lambda **_: True,
        resumo=lambda **_: {},
    )
    return ProcessoShadow(
        _settings(tmp_path), ciclo, caminho_do_diario=tmp_path / "diario.jsonl"
    )


class TestDuracaoDoCiclo:
    async def test_o_relato_publica_p50_e_max_da_janela_e_zera(self, tmp_path, monkeypatch):
        monkeypatch.setattr("pulsearb.live.shadow.CADENCIA_DO_MAKER_S", 0.001)
        processo = _processo(tmp_path)

        await asyncio.wait_for(
            processo.laco_de_cotacao(time.monotonic() + 0.05), timeout=2.0
        )

        duracao = processo.estado(avancar_vigilia=True)["duracao_do_ciclo_ms"]
        assert duracao["n"] >= 1
        assert 0.0 <= duracao["p50"] <= duracao["max"]
        # A janela do relato fechou: o próximo não herda as passadas.
        assert processo.estado()["duracao_do_ciclo_ms"] == {
            "p50": None, "max": None, "n": 0,
        }

    def test_p50_e_max_saem_das_amostras(self, tmp_path):
        processo = _processo(tmp_path)
        processo._duracoes_do_ciclo_ns = [3_000_000, 1_000_000, 2_000_000, 9_000_000]

        assert processo.estado()["duracao_do_ciclo_ms"] == {
            "p50": 2.0, "max": 9.0, "n": 4,
        }


class TestEscritorUnico:
    """T4: o laço maker não tem trava — a correção dele depende de UMA tarefa
    só chamar o que muda o estado, e de a reconciliação de arranque rodar
    antes de as tarefas subirem. Isto prende a fiação."""

    ESCRITORES = frozenset({"passo", "recolher_se_o_livro_andou", "ver_prints_entre_passadas"})
    AJUDANTES_DO_ESCRITOR = frozenset(
        {"_recolher_entre_passadas", "_ver_prints_entre_passadas"}
    )

    def test_so_o_laco_de_cotacao_escreve_no_laco_maker(self):
        arvore = ast.parse(FONTE_DO_SHADOW.read_text(encoding="utf-8"))
        classe = _corpo_da_classe(arvore, "ProcessoShadow")
        onde: dict[str, set[str]] = {}
        chama_o_sono: set[str] = set()
        chamadas_dos_ajudantes: dict[str, set[str]] = {}
        for metodo in classe.body:
            if not isinstance(metodo, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for no in ast.walk(metodo):
                if not (isinstance(no, ast.Call) and isinstance(no.func, ast.Attribute)):
                    continue
                alvo = ast.unparse(no.func.value)
                if alvo == "self.laco_maker" and (
                    no.func.attr in self.ESCRITORES
                    or no.func.attr == "reconciliar_no_arranque"
                ):
                    onde.setdefault(no.func.attr, set()).add(metodo.name)
                if alvo == "self" and no.func.attr == "_dormir_medindo_markout":
                    chama_o_sono.add(metodo.name)
                if alvo == "self" and no.func.attr in self.AJUDANTES_DO_ESCRITOR:
                    chamadas_dos_ajudantes.setdefault(no.func.attr, set()).add(metodo.name)

        for escritor in self.ESCRITORES:
            assert onde.get(escritor, set()) <= {
                "laco_de_cotacao", "_dormir_medindo_markout",
                *self.AJUDANTES_DO_ESCRITOR,
            }, (escritor, onde.get(escritor))
        for ajudante in self.AJUDANTES_DO_ESCRITOR:
            assert chamadas_dos_ajudantes.get(ajudante, set()) == {
                "_dormir_medindo_markout"
            }
        assert onde["passo"] == {"laco_de_cotacao"}
        assert chama_o_sono == {"laco_de_cotacao"}
        assert onde["reconciliar_no_arranque"] == {"_reconciliar_maker_no_arranque"}

    def test_das_tarefas_do_run_so_a_de_cotacao_chega_aos_escritores(self):
        arvore = ast.parse(FONTE_DO_SHADOW.read_text(encoding="utf-8"))
        classe = _corpo_da_classe(arvore, "ProcessoShadow")
        run = next(
            m for m in classe.body
            if isinstance(m, ast.AsyncFunctionDef) and m.name == "run"
        )
        tarefas = [
            no.args[0].func.attr
            for no in ast.walk(run)
            if isinstance(no, ast.Call)
            and ast.unparse(no.func) == "asyncio.create_task"
            and isinstance(no.args[0], ast.Call)
            and isinstance(no.args[0].func, ast.Attribute)
        ]
        assert "laco_de_cotacao" in tarefas
        escritoras = {"laco_de_cotacao", "_dormir_medindo_markout"}
        assert [t for t in tarefas if t in escritoras] == ["laco_de_cotacao"]

        # E a reconciliação de arranque vem ANTES da primeira tarefa.
        linhas = [
            no.lineno for no in ast.walk(run)
            if isinstance(no, ast.Call)
            and ast.unparse(no.func) == "self._reconciliar_maker_no_arranque"
        ]
        primeira_tarefa = min(
            no.lineno for no in ast.walk(run)
            if isinstance(no, ast.Call) and ast.unparse(no.func) == "asyncio.create_task"
        )
        assert linhas and max(linhas) < primeira_tarefa


class TestLivroMudoPontaAPonta:
    """T5: `LivrosAoVivo` com o token mudo além do limite → `_livro_para_o_
    maker` devolve `None` → o `passo` não cota e conta o motivo."""

    def _livros(self):
        from pulsearb.live.livros import LivrosAoVivo

        livros = LivrosAoVivo()
        for token in ("tok-up", "tok-down"):
            livros.aplicar(
                {
                    "event_type": "book",
                    "asset_id": token,
                    "bids": [{"price": "0.49", "size": "500"}, {"price": "0.48", "size": "500"}],
                    "asks": [{"price": "0.51", "size": "500"}, {"price": "0.52", "size": "500"}],
                },
                ts_ns=0,
            )
        return livros

    async def _passo(self, processo, agora_s):
        return await processo.laco_maker.passo(
            [_janela()],
            livro_de=processo._livro_para_o_maker,
            agora_epoch=1000.0,
            agora_ns=int(agora_s * 1e9),
        )

    async def test_token_mudo_nao_cota_e_conta_livro_indisponivel(self, tmp_path):
        from pulsearb.live.laco_maker import MOTIVOS_DO_LACO

        livros = self._livros()
        processo = _processo(tmp_path, livros=livros)
        processo.laco_maker.portao = _PortaoDuble()
        silencio = livros.silencio_do_token_s

        assert processo._livro_para_o_maker("tok-up", agora_ns=int((silencio + 1) * 1e9)) is None
        efeitos = await self._passo(processo, silencio + 1)

        assert efeitos == []
        assert processo.laco_maker.abertas == {}
        assert processo.laco_maker.motivos == {MOTIVOS_DO_LACO.LIVRO_INDISPONIVEL: 1}

    async def test_controle_o_mesmo_livro_fresco_cota(self, tmp_path):
        """Sem este controle o teste acima passaria com um livro que nunca
        serviu para cotar."""
        processo = _processo(tmp_path, livros=self._livros())
        processo.laco_maker.portao = _PortaoDuble()

        efeitos = await self._passo(processo, 1.0)

        assert [e.resultado for e in efeitos] == [ResultadoDaAcao.COLOCADA]


# ─────────────────────────────────────────────────────────────────── S1
class TestProvaDeAusenciaDoSombra:
    def test_o_sombra_usa_a_constante_e_nao_o_literal(self):
        from pulsearb.execution.cliente import MOTIVO_ID_NAO_REPOUSAVA

        arvore = ast.parse(FONTE_DO_SOMBRA.read_text(encoding="utf-8"))
        literais = [
            no.lineno for no in ast.walk(arvore)
            if isinstance(no, ast.Constant) and no.value == MOTIVO_ID_NAO_REPOUSAVA
        ]
        assert literais == []

    async def test_id_desconhecido_volta_com_a_prova_de_ausencia(self, tmp_path):
        cliente = ClienteSombraDeOrdens(caminho_do_diario=tmp_path / "d.jsonl")

        resultado = await cliente.cancelar("nunca-existiu")

        assert resultado.fora_do_livro is True
