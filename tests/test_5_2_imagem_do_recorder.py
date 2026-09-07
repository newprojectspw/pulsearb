"""5.2 — o container sobe? A pergunta que nenhum build local pode responder aqui.

Não há Docker, Colima nem Podman nesta máquina (medido 2026-08-31 e de novo em
2026-09-06), e o token não tem escopo `workflow` para subir um job de build no
CI. Então o 5.2 depende de um build que ninguém consegue rodar daqui — e o
quadro registra, com razão, que *"o que sobra não está verificado: o resto saiu
de leitura, não de build"*.

O QUE ESTE ARQUIVO FAZ, E O QUE ELE NÃO SUBSTITUI
──────────────────────────────────────────────────
Ele não constrói imagem. Ele ataca **um** modo de falha específico do deploy,
que é invisível na leitura do Dockerfile e caro na VPS:

    o entrypoint importa algo que a imagem não tem, e o container morre
    no arranque com `ImportError` — depois do build passar.

A imagem roda `pip install .`, ou seja **só as dependências de runtime** do
`pyproject.toml`. O venv de desenvolvimento tem muito mais. Um import novo em
qualquer módulo do caminho do recorder passa verde aqui e morre lá.

A VERIFICAÇÃO É POR SIMULAÇÃO, NÃO POR LISTA
─────────────────────────────────────────────
Comparar imports contra uma lista de deps erraria nos dois sentidos: não veria
import transitivo, e acusaria import OPCIONAL que já é guardado por
`try/except ImportError` (o `httpx` faz exatamente isso com o CLI dele —
`click`, `rich` e `pygments` são `extra == 'cli'`, e sem eles o `httpx` segue
funcionando).

Então o teste **finge ser o container**: calcula o fecho transitivo das deps de
runtime, torna inimportável tudo que está fora dele, e importa o entrypoint. Se
importar, o container sobe. Se um import novo não for guardado, quebra aqui —
na suíte, não na VPS.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

#: O que o `ENTRYPOINT` do `deploy/Dockerfile` executa.
ENTRYPOINT = "pulsearb.recorder.__main__"

_SIMULA_O_CONTAINER = '''
import sys, tomllib, importlib, importlib.abc
from importlib.metadata import packages_distributions, requires
from packaging.requirements import Requirement

RAIZ = %(raiz)r
ALVO = %(alvo)r

def _norm(nome):
    return nome.lower().replace("_", "-")

with open(RAIZ + "/pyproject.toml", "rb") as arquivo:
    declaradas = tomllib.load(arquivo)["project"]["dependencies"]

# Fecho TRANSITIVO: a imagem recebe as deps declaradas E o que elas puxam.
# Parar nas diretas acusaria `idna` (que vem via httpx) como ausente.
fecho, pilha = set(), [_norm(Requirement(d).name) for d in declaradas]
while pilha:
    nome = pilha.pop()
    if nome in fecho:
        continue
    fecho.add(nome)
    try:
        exigidas = requires(nome) or []
    except Exception:
        continue
    for bruta in exigidas:
        try:
            req = Requirement(bruta)
        except Exception:
            continue
        # `extra == 'cli'` e afins NAO entram: a imagem instala `.`, sem extras.
        if req.marker is not None and not req.marker.evaluate({"extra": ""}):
            continue
        pilha.append(_norm(req.name))

mapa = packages_distributions()
def _no_container(modulo):
    dists = mapa.get(modulo)
    if not dists:            # stdlib ou o proprio pulsearb (instalado por pip install .)
        return True
    return any(_norm(d) in fecho for d in dists)

class Bloqueador(importlib.abc.MetaPathFinder):
    """Torna inimportavel o que a imagem nao teria."""
    def find_spec(self, fullname, path=None, target=None):
        raiz_do_modulo = fullname.split(".")[0]
        if raiz_do_modulo in sys.stdlib_module_names or raiz_do_modulo == "pulsearb":
            return None
        if _no_container(raiz_do_modulo):
            return None
        raise ImportError(
            "modulo ausente na imagem do recorder: " + fullname
            + " (fornecido por " + str(mapa.get(raiz_do_modulo)) + ")"
        )

# Descarrega o que este proprio script importou e a imagem NAO teria (o
# `packaging`, por exemplo). Sem isto, um import do recorder acharia o modulo
# no cache e passaria por cima do bloqueio — o teste diria OK sem testar.
for carregado in list(sys.modules):
    raiz_do_modulo = carregado.split(".")[0]
    if raiz_do_modulo in sys.stdlib_module_names or raiz_do_modulo == "pulsearb":
        continue
    if not _no_container(raiz_do_modulo):
        sys.modules.pop(carregado, None)

sys.meta_path.insert(0, Bloqueador())
importlib.import_module(ALVO)
print("OK")
'''


def _importar_como_no_container(alvo: str) -> subprocess.CompletedProcess[str]:
    """Importa `alvo` num processo onde só as deps da imagem existem.

    Subprocesso porque o bloqueio mexe em `sys.meta_path` e em `sys.modules`:
    fazer isso dentro da suíte contaminaria os outros testes.
    """
    codigo = _SIMULA_O_CONTAINER % {"raiz": str(RAIZ), "alvo": alvo}
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(codigo)],
        cwd=RAIZ,
        capture_output=True,
        text=True,
        timeout=120,
    )


class TestOEntrypointSobeComAsDepsDaImagem:
    def test_o_recorder_importa_so_com_as_deps_de_runtime(self):
        """O modo de falha que só apareceria na VPS.

        `pip install .` instala as 11 deps de runtime e o que elas puxam. Se
        qualquer módulo do caminho do recorder importar algo de fora disso sem
        guarda, o build passa e o container morre no arranque.
        """
        resultado = _importar_como_no_container(ENTRYPOINT)

        assert resultado.returncode == 0, (
            f"\n`python -m pulsearb.recorder` NAO importaria na imagem.\n"
            f"Este e o erro que apareceria na VPS depois de um build verde:\n\n"
            f"{resultado.stderr[-1500:]}\n"
            "Ou a dependencia entra em [project.dependencies] do pyproject.toml,\n"
            "ou o import passa a ser opcional com try/except ImportError."
        )
        assert "OK" in resultado.stdout

    def test_o_bloqueador_realmente_bloqueia(self):
        """O teste do teste.

        Um bloqueador que não bloqueia nada faria o caso acima passar sempre —
        seria o `cobertura_da_gravacao` de novo: nota máxima para a medição que
        não aconteceu. Aqui se prova que um módulo comprovadamente fora do
        runtime (`pytest`, que é dev) É recusado.
        """
        resultado = _importar_como_no_container("pytest")

        assert resultado.returncode != 0
        assert "ausente na imagem do recorder" in resultado.stderr


class TestOQueODockerfilePromete:
    def test_o_entrypoint_do_dockerfile_e_o_modulo_que_este_teste_confere(self):
        """Se o ENTRYPOINT mudar e este teste continuar conferindo o módulo
        antigo, ele passa a proteger o que não roda mais."""
        dockerfile = (RAIZ / "deploy" / "Dockerfile").read_text(encoding="utf-8")

        assert 'ENTRYPOINT ["python", "-m", "pulsearb.recorder"]' in dockerfile
        assert ENTRYPOINT.startswith("pulsearb.recorder")

    def test_a_imagem_roda_como_nao_root_e_o_data_e_dele(self):
        """O defeito achado na leitura do M2: `VOLUME` cria o ponto como root,
        o container roda como não-root, e a primeira escrita morreria com
        `PermissionError` — que com `--restart=always` vira laço de reinício
        que o `docker ps` mostra como 'rodando'."""
        dockerfile = (RAIZ / "deploy" / "Dockerfile").read_text(encoding="utf-8")

        assert "USER pulsearb" in dockerfile
        assert "chown" in dockerfile
        # o `mkdir`+`chown` tem de vir ANTES do VOLUME, senão não adianta
        assert dockerfile.index("chown") < dockerfile.index('VOLUME ["/data"]')
