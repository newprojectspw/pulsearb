"""O perfil do 4.2 não pode subir uns tetos e esquecer outros.

Este arquivo existe por causa de um defeito medido, não por completude:
em 2026-09-21 o disjuntor estava armado havia dois dias e **toda rodada
maker recusava cotar**, porque o `comum.env` subiu quatro tetos para escala
de maker e deixou o quinto no default do taker (runbook §10.1l).

O modo de falhar é o que torna isto perigoso: nada falha, nada alarma. A
rodada segue com `falhou: null` e feeds saudáveis, publicando
`cotacoes_repousando: 0` — que é indistinguível de mercado quieto para quem
não abrir o dicionário `motivos`.
"""

from pathlib import Path

from pulsearb.settings import Settings

COMUM = Path(__file__).resolve().parents[1] / "deploy" / "rodadas" / "comum.env"


def _perfil() -> dict[str, str]:
    """As variáveis do perfil do ensaio, sem comentários nem linhas vazias."""
    valores: dict[str, str] = {}
    for linha in COMUM.read_text().splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, _, valor = linha.partition("=")
        valores[chave.strip()] = valor.strip()
    return valores


def test_o_perfil_sobe_o_stake_acima_do_default():
    """Pré-condição dos testes abaixo, e vale como documentação.

    Se um dia o perfil voltar ao stake do taker, os testes seguintes deixam
    de fazer sentido — e é melhor descobrir isso aqui do que vê-los passar
    por vacuidade.
    """
    perfil = _perfil()
    padrao = Settings().risk
    stake = float(perfil["PULSEARB_RISK__STAKE_MAX_POR_TRADE_USDC"])
    assert stake > padrao.stake_max_por_trade_usdc


def test_quem_sobe_o_stake_sobe_o_disjuntor_junto():
    """O defeito do §10.1l, guardado.

    A razão não é escolha nova: o default carrega `25 = 5 x stake de 5`, que
    é "cinco trades ruins e para". Este teste exige que a razão sobreviva à
    mudança de escala — não que o valor seja 5000.
    """
    perfil = _perfil()
    chave = "PULSEARB_RISK__PERDA_MAX_DIARIA_USDC"
    assert chave in perfil, (
        "o perfil sobe o stake e não sobe o disjuntor: é o defeito do "
        "§10.1l, em que o ensaio recusou cotar por dois dias sem nada falhar"
    )

    padrao = Settings().risk
    razao_do_default = padrao.perda_max_diaria_usdc / padrao.stake_max_por_trade_usdc
    stake = float(perfil["PULSEARB_RISK__STAKE_MAX_POR_TRADE_USDC"])
    teto = float(perfil[chave])

    assert teto >= razao_do_default * stake, (
        f"disjuntor em {teto:.0f} USDC para stake de {stake:.0f}: menos que "
        f"os {razao_do_default:.0f}x que o default carrega. Com cotação "
        "grande, um teto pequeno arma no primeiro dia — e o disjuntor GRUDA, "
        "então não custa um dia, custa a janela de 14"
    )


def test_o_disjuntor_do_perfil_continua_sendo_um_teto():
    """Subir não pode virar desligar.

    Um teto alto o bastante para nunca armar não exercita o portão — e
    exercitar o portão é metade do motivo de o SHADOW existir
    (`caminho_do_registro_do_modo`, achado do Codex no #52).
    """
    perfil = _perfil()
    teto = float(perfil["PULSEARB_RISK__PERDA_MAX_DIARIA_USDC"])
    exposicao = float(perfil["PULSEARB_RISK__EXPOSICAO_MAX_USDC"])
    assert 0 < teto < exposicao, (
        "o disjuntor tem de armar antes de a exposição inteira virar perda"
    )
