"""5.2 — o job de build, agora DENTRO de `.github/workflows/ci.yml`.

Ele morou em `deploy/ci-job-docker.yml` de 2026-09-06 a 2026-09-12, esperando
um token com escopo `workflow` — sem esse escopo, um push que toque em
`.github/workflows/` é recusado INTEIRO, não só o arquivo. Aplicado o escopo,
a cópia de espera foi apagada: duas cópias do mesmo YAML divergem, e a que
ninguém roda é sempre a que apodrece.

O teste de espera (`test_o_ci_ainda_nao_tem_o_job...`) cumpriu o papel e saiu:
ele existia para falhar no dia da aplicação e lembrar de fazer esta limpeza.

**O que sobrou, e por que não foi apagado junto.** A instrução daquele teste
mandava apagar este arquivo inteiro. As outras asserções não são sobre a
espera — são sobre o job CONTINUAR verdadeiro: que a action siga fixada por
SHA, que o UID conferido seja o do Dockerfile, que o ponto de montagem seja o
do `VOLUME`. Um job que confere `10001` depois de o Dockerfile passar a criar
`10002` falha dizendo a coisa errada, e isso é pior que não conferir. Essas
guardas passaram a ler o `ci.yml`.
"""

from __future__ import annotations

from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent.parent
DOCKERFILE = RAIZ / "deploy" / "Dockerfile"
CI = RAIZ / ".github" / "workflows" / "ci.yml"


def _ci() -> dict:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))


class TestOJobEstaNoCI:
    def test_o_ci_e_yaml_valido(self):
        """YAML quebrado aqui derruba o CI inteiro, inclusive o job `testes`
        que já funcionava. Foi o que aconteceu na primeira tentativa de
        aplicar: o recorte do bloco casou com a prosa de um comentário em vez
        da linha do job, e o arquivo saiu inválido."""
        assert _ci() is not None

    def test_define_um_job_docker_em_linux(self):
        """`ubuntu-latest` não é detalhe: o cenário de bind mount do RUNBOOK
        NÃO reproduz no macOS com Colima, onde a camada de mount traduz a
        escrita e um UID 10001 grava em diretório `root:root`. Só Linux real
        responde se o `chown` do runbook é mesmo necessário."""
        jobs = _ci()["jobs"]

        assert "docker" in jobs
        assert jobs["docker"]["runs-on"] == "ubuntu-latest"

    def test_o_job_testes_sobreviveu_a_aplicacao(self):
        """Colar o bloco no fim do arquivo é a forma mais fácil de comer o job
        anterior por um erro de indentação."""
        jobs = _ci()["jobs"]

        assert "testes" in jobs
        assert [p.get("name") for p in jobs["testes"]["steps"] if p.get("name")] == [
            "instalar",
            "ruff",
            "pytest",
        ]

    def test_fixa_a_action_por_SHA_como_o_resto_do_ci(self):
        """Tag pode ser movida por quem controla o repositório da action."""
        passos = _ci()["jobs"]["docker"]["steps"]
        checkout = next(p for p in passos if "uses" in p)

        assert checkout["uses"].startswith("actions/checkout@11d5960a")
        assert checkout["with"]["persist-credentials"] is False


class TestOQueOJobAFIRMASobreODockerfile:
    def test_o_UID_conferido_e_o_do_Dockerfile(self):
        """Se o Dockerfile trocar o UID e o job continuar conferindo 10001,
        ele falharia dizendo a coisa errada."""
        texto = CI.read_text(encoding="utf-8")

        assert "--uid 10001" in DOCKERFILE.read_text(encoding="utf-8")
        assert 'test "$uid" = "10001"' in texto

    def test_o_caminho_do_dockerfile_existe(self):
        assert "-f deploy/Dockerfile" in CI.read_text(encoding="utf-8")
        assert DOCKERFILE.exists()

    def test_o_ponto_de_montagem_conferido_e_o_do_VOLUME(self):
        """O job escreve em `/data` porque é lá que o `VOLUME` aponta."""
        assert 'VOLUME ["/data"]' in DOCKERFILE.read_text(encoding="utf-8")
        assert "/data/prova" in CI.read_text(encoding="utf-8")

    def test_a_copia_de_espera_foi_apagada(self):
        """Duas cópias do mesmo YAML divergem, e a que ninguém roda apodrece."""
        assert not (RAIZ / "deploy" / "ci-job-docker.yml").exists(), (
            "`deploy/ci-job-docker.yml` voltou a existir. Ele era a copia de\n"
            "ESPERA, aplicada ao ci.yml em 2026-09-12. Se o job precisa mudar,\n"
            "mude no `.github/workflows/ci.yml`, que e o que roda."
        )
