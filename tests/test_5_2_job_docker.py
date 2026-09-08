"""5.2 — o job de build, guardado em `deploy/ci-job-docker.yml` até poder ir.

Um arquivo de workflow que espera para ser aplicado é exatamente o tipo de
coisa que apodrece: ninguém o roda, então ninguém percebe quando ele passa a
citar um caminho que mudou de nome ou um UID que mudou de valor.

Estes testes fazem o mínimo que mantém o arquivo aplicável: ele tem de ser
YAML válido, tem de encaixar em `jobs:` do `ci.yml`, e o que ele afirma sobre
o Dockerfile tem de continuar verdade.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent.parent
JOB = RAIZ / "deploy" / "ci-job-docker.yml"
DOCKERFILE = RAIZ / "deploy" / "Dockerfile"
CI = RAIZ / ".github" / "workflows" / "ci.yml"


def _job_carregado() -> dict:
    """O bloco, desindentado, como o YAML que ele vira dentro de `jobs:`."""
    return yaml.safe_load(textwrap.dedent(JOB.read_text(encoding="utf-8")))


class TestOJobEAplicavel:
    def test_e_yaml_valido(self):
        """Colar YAML quebrado no `ci.yml` derruba o CI inteiro, inclusive o
        job `testes` que hoje funciona."""
        assert _job_carregado() is not None

    def test_define_um_job_chamado_docker(self):
        carregado = _job_carregado()

        assert "docker" in carregado
        assert carregado["docker"]["runs-on"] == "ubuntu-latest"

    def test_a_indentacao_encaixa_em_jobs_do_ci(self):
        """O bloco é para colar dentro de `jobs:`, então cada linha de conteúdo
        começa com dois espaços — o mesmo nível do job `testes`."""
        linhas = [
            linha
            for linha in JOB.read_text(encoding="utf-8").splitlines()
            if linha.strip() and not linha.lstrip().startswith("#")
        ]

        assert linhas[0] == "  docker:"
        assert all(linha.startswith("  ") for linha in linhas)

    def test_fixa_a_action_por_SHA_como_o_resto_do_ci(self):
        """Tag pode ser movida por quem controla o repositório da action."""
        texto = JOB.read_text(encoding="utf-8")

        assert "actions/checkout@11d5960a" in texto
        assert "persist-credentials: false" in texto


class TestOQueOJobAFIRMASobreODockerfile:
    def test_o_UID_conferido_e_o_do_Dockerfile(self):
        """Se o Dockerfile trocar o UID e o job continuar conferindo 10001,
        ele falharia dizendo a coisa errada."""
        assert "--uid 10001" in DOCKERFILE.read_text(encoding="utf-8")
        assert 'test "$uid" = "10001"' in JOB.read_text(encoding="utf-8")

    def test_o_caminho_do_dockerfile_existe(self):
        texto = JOB.read_text(encoding="utf-8")

        assert "-f deploy/Dockerfile" in texto
        assert DOCKERFILE.exists()

    def test_o_ponto_de_montagem_conferido_e_o_do_VOLUME(self):
        """O job escreve em `/data` porque é lá que o `VOLUME` aponta."""
        assert 'VOLUME ["/data"]' in DOCKERFILE.read_text(encoding="utf-8")
        assert "/data/prova" in JOB.read_text(encoding="utf-8")


class TestAindaNaoFoiAplicado:
    def test_o_ci_ainda_nao_tem_o_job_e_o_quadro_diz_isso(self):
        """Quando alguém aplicar o job, ESTE teste falha — e é o lembrete de
        apagar este arquivo de espera e atualizar a linha 5.2 do quadro, em vez
        de deixar as duas cópias divergirem.
        """
        ci = yaml.safe_load(CI.read_text(encoding="utf-8"))

        assert "docker" not in ci["jobs"], (
            "O job `docker` foi aplicado no ci.yml — otimo.\n"
            "Agora: apague `deploy/ci-job-docker.yml` e este arquivo de teste,\n"
            "e atualize a linha 5.2 do quadro com o resultado do build real,\n"
            "no MESMO commit (Regra 1 do CLAUDE.md)."
        )
