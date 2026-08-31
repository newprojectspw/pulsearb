"""A Regra 1 do CLAUDE.md, executável.

    "O quadro anda no MESMO commit que o conserto. Quadro atualizado num
    commit separado, 'depois', é quadro que fica para trás — e um quadro que
    mente sobre o que está pronto é pior que não ter quadro, porque alguém
    decide LIVE a partir dele."

O `ESTADO_PARA_LIVE.md` cita contagens de teste como EVIDÊNCIA de que um item
está fechado — "47 testes", "22 testes", "108 no total". Enquanto nada
conferia esses números, eles envelheciam em silêncio: quem adiciona um teste
raramente lembra de abrir o quadro, e a linha continua afirmando o número
antigo com a mesma cara de evidência.

O que este arquivo faz é barato e chato de propósito: se a contagem mudou, ele
falha e diz qual linha do quadro precisa mudar junto. É o custo de manter o
quadro utilizável para a decisão que ele existe para sustentar.

**Por que a tabela mora aqui, e não é extraída do texto.** Extrair
"47 testes" do markdown exigiria adivinhar a qual arquivo cada número se
refere, e a resposta erraria em silêncio quando o texto fosse reescrito. Com a
tabela explícita, mudar um número exige tocar nos DOIS lugares — que é
exatamente o comportamento que a Regra 1 pede.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
QUADRO = RAIZ / "docs" / "ESTADO_PARA_LIVE.md"

#: arquivo de teste → (contagem afirmada no quadro, item que a cita)
#:
#: Mudou a contagem? Mude AQUI e no `ESTADO_PARA_LIVE.md`, no mesmo commit.
#: A falha deste teste é o lembrete de que o quadro ficou para trás.
CONTAGENS_NO_QUADRO: dict[str, tuple[int, str]] = {
    "test_m4_portao_de_risco.py": (47, "3.1/3.6 — os 8 portões"),
    "test_m4_cliente_de_ordens.py": (47, "3.5 — cliente de ordens"),
    "test_m4_auth_clob.py": (34, "3.2 — auth do CLOB"),
    "test_m4_struct_da_ordem.py": (35, "3.2 — struct EIP-712"),
    "test_m4_autorizacao_para_live.py": (22, "3.4 — trava tripla do LIVE"),
    "test_m4_shadow.py": (15, "3.3 — modo SHADOW"),
    "test_m4_sincronia_do_relogio.py": (14, "5.4 — NTP"),
    "test_relogio_do_servidor.py": (20, "3.10 — relógio do servidor"),
    "test_m4_travas_que_faltavam.py": (27, "3.12 — travas novas"),
}

#: O 3.12 publica a SOMA destes quatro. Somar na mão já saiu errado em
#: documento deste projeto, então a soma é conferida em vez de copiada.
PARCELAS_DO_3_12 = (
    "test_m4_portao_de_risco.py",
    "test_m4_travas_que_faltavam.py",
    "test_relogio_do_servidor.py",
    "test_m4_sincronia_do_relogio.py",
)
TOTAL_DO_3_12 = 108


def _coletados(arquivo: str) -> int:
    """Quantos testes o pytest coleta neste arquivo.

    Coleta em subprocesso porque é a MESMA conta que a suíte faz — contar
    `def test_` no fonte daria outro número em tudo que usa `parametrize`, e
    seria um segundo jeito de contar convivendo com o primeiro. Duas contas
    para a mesma coisa é como se descobre uma divergência tarde demais.
    """
    resultado = subprocess.run(
        [sys.executable, "-m", "pytest", f"tests/{arquivo}", "--collect-only", "-q"],
        cwd=RAIZ,
        capture_output=True,
        text=True,
        timeout=120,
    )
    # O `-q` do pytest resume como `tests/arquivo.py: N` — e NÃO como
    # "N tests collected", que é a forma do modo verboso. A primeira versão
    # deste arquivo procurava a segunda, não achava, e caía num `skip`: os
    # nove casos passaram VERDES sem conferir nada. Daí a ausência de match
    # ser falha, e não skip — teste que não mede tem de falhar alto, senão
    # vira o `cobertura_da_gravacao` de novo (dar nota máxima ao caso em que
    # a medição não existe).
    achado = re.search(rf"{re.escape(arquivo)}:\s*(\d+)", resultado.stdout)
    if achado is None:
        raise AssertionError(
            f"não consegui ler a coleta de {arquivo} — este teste não pode\n"
            f"passar sem medir. Saída do pytest:\n{resultado.stdout[-400:]}"
        )
    return int(achado.group(1))


@pytest.mark.parametrize(
    ("arquivo", "afirmado", "item"),
    [(a, n, i) for a, (n, i) in CONTAGENS_NO_QUADRO.items()],
)
def test_a_contagem_do_quadro_bate_com_a_suite(arquivo, afirmado, item):
    """O número que o quadro publica como evidência é o número que existe."""
    real = _coletados(arquivo)

    assert real == afirmado, (
        f"\n{arquivo}: a suíte tem {real} testes, o quadro afirma {afirmado}.\n"
        f"O item afetado é o {item}.\n\n"
        "Isto não é falha de teste — é o quadro ficando para trás. Atualize\n"
        "docs/ESTADO_PARA_LIVE.md E a tabela deste arquivo, NO MESMO COMMIT\n"
        "(Regra 1 do CLAUDE.md)."
    )


def test_o_total_do_3_12_e_a_soma_das_parcelas():
    """108 = 47 + 27 + 20 + 14. Conferido, não copiado."""
    soma = sum(CONTAGENS_NO_QUADRO[a][0] for a in PARCELAS_DO_3_12)

    assert soma == TOTAL_DO_3_12, (
        f"o 3.12 publica {TOTAL_DO_3_12}, mas as parcelas somam {soma}"
    )


def test_os_numeros_aparecem_no_texto_do_quadro():
    """Não basta a tabela daqui estar certa: o TEXTO tem de trazer o número.

    Sem isto, alguém poderia manter esta tabela em dia e o markdown
    desatualizado — e é o markdown que a pessoa lê ao decidir sobre o LIVE.
    """
    texto = QUADRO.read_text(encoding="utf-8")
    ausentes = [
        f"{n} ({item})"
        for _, (n, item) in CONTAGENS_NO_QUADRO.items()
        if not re.search(rf"\b{n}\b", texto)
    ]

    assert not ausentes, (
        "contagens que esta tabela afirma mas que não aparecem no quadro: "
        + ", ".join(ausentes)
    )


#: Onde um caminho citado no quadro pode estar. O quadro escreve
#: `risk/gates.py` querendo dizer `src/pulsearb/risk/gates.py`, e
#: `ci.yml` querendo dizer `.github/workflows/ci.yml` — a citação é a que o
#: leitor usa para achar a evidência, então todas as formas contam.
PREFIXOS_DE_BUSCA = (
    "",
    "src/pulsearb",
    "docs",
    "tests",
    "scripts",
    "deploy",
    ".github/workflows",
)

#: Extensões que valem conferir. `.json` fica de fora de propósito: o quadro
#: cita relatórios (`M2_25AGO.json`) que moram em `relatorios/`, um diretório
#: que não é versionado — exigir que existam faria o teste falhar em clone
#: novo, que é o oposto do que ele existe para pegar.
EXTENSOES_CONFERIDAS = ("py", "sh", "yml", "md")


def test_os_arquivos_citados_como_evidencia_existem():
    """Citação que não resolve é evidência que ninguém consegue conferir.

    O quadro aponta arquivo e símbolo o tempo todo — é assim que ele sustenta
    um ✅. Um arquivo renomeado deixa a linha apontando para o nada, e o
    defeito é invisível: o texto continua com a mesma cara de evidência, e só
    quem for procurar descobre que não há o que ler.
    """
    texto = QUADRO.read_text(encoding="utf-8")
    padrao = rf"`([a-z_][a-z0-9_/]*\.(?:{'|'.join(EXTENSOES_CONFERIDAS)}))`"
    citados = sorted(set(re.findall(padrao, texto)))

    assert citados, "nenhum arquivo citado — o padrão de busca quebrou"

    sumidos = [
        c
        for c in citados
        if not any((RAIZ / p / c).exists() for p in PREFIXOS_DE_BUSCA)
    ]

    assert not sumidos, (
        "o quadro cita como evidência arquivos que não existem: "
        + ", ".join(sumidos)
        + "\nRenomeou algo? A linha do quadro que aponta para ele precisa "
        "mudar no MESMO commit (Regra 1 do CLAUDE.md)."
    )


def test_o_quadro_existe_e_tem_a_legenda_dos_simbolos():
    """A legenda é o que dá sentido a ✅/🟡/❌ — sem ela o quadro não se lê."""
    texto = QUADRO.read_text(encoding="utf-8")

    for simbolo in ("✅", "🟡", "❌", "⬜"):
        assert simbolo in texto, f"o quadro perdeu o símbolo {simbolo}"
    assert "Como ler" in texto, "o quadro perdeu a legenda de como lê-lo"
