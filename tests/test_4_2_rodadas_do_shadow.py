"""As quatro rodadas do 4.2: cada `.env` liga a regra que diz ligar.

## O defeito que estes testes impedem

Uma variável com o nome errado no arquivo de ambiente **não dá erro**: o
pydantic simplesmente não a encontra e usa o default, que é "desligada". A
instância sobe verde, relata verde, roda 14 dias — e mede a rodada BASE com
o nome da regra no rótulo. Nada no caminho denuncia: nem o systemd, nem o
`Settings`, nem o log, porque não houve falha nenhuma.

O único jeito de pegar isso antes dos 14 dias é carregar o arquivo de verdade
no `Settings` de verdade e conferir o que saiu do outro lado. É o que estes
testes fazem.

O leitor da rodada (`scripts/resumo_da_rodada_maker.py`) pega o mesmo engano
DEPOIS — ele imprimiria "rodada BASE" para a instância `@ancora`. Aqui é
antes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from pulsearb.settings import Settings

RAIZ = Path(__file__).resolve().parents[1]
RODADAS = RAIZ / "deploy" / "rodadas"
UNIT = RAIZ / "deploy" / "pulsearb-shadow-maker@.service"

#: O perfil do ensaio, igual nas quatro. Não é uma rodada: é o que elas
#: compartilham, e mora em arquivo de ambiente porque `EnvironmentFile` vence
#: `Environment=` SEMPRE — na unit, o `.env` da máquina o silenciaria.
COMUM = "comum"

#: O que o perfil compartilhado tem de produzir. Um `.env` de máquina que o
#: vencesse mudaria o ensaio das quatro instâncias por 14 dias.
PERFIL: dict[str, object] = {
    "tamanho_da_cotacao_maker_shares": 1000.0,
    "top_de_pools_de_reward": 60,
}

#: Qual regra cada rodada mede, e com que valor. O que está aqui é a INTENÇÃO
#: declarada; o teste confere que o arquivo produz exatamente isso.
ESPERADO: dict[str, dict[str, object]] = {
    "base": {
        "maker_recolhe_quando_o_livro_anda": False,
        "maker_ticks_abaixo_do_microprice": None,
        "maker_pausa_apos_fill_toxico_s": None,
    },
    "recolher": {
        "maker_recolhe_quando_o_livro_anda": True,
        "maker_ticks_abaixo_do_microprice": None,
        "maker_pausa_apos_fill_toxico_s": None,
    },
    "ancora": {
        "maker_recolhe_quando_o_livro_anda": False,
        "maker_ticks_abaixo_do_microprice": 1,
        "maker_pausa_apos_fill_toxico_s": None,
    },
    "pausa": {
        "maker_recolhe_quando_o_livro_anda": False,
        "maker_ticks_abaixo_do_microprice": None,
        "maker_pausa_apos_fill_toxico_s": 30.0,
    },
}


def _ler_env(caminho: Path) -> dict[str, str]:
    """`KEY=VALUE` por linha, `#` comenta — o subconjunto que estes arquivos usam.

    Não é um parser de `EnvironmentFile` completo (o systemd aceita aspas,
    continuação de linha, `\\` de escape). Os quatro arquivos ficam DENTRO
    deste subconjunto de propósito: um arquivo que precise das regras de
    citação do systemd para ser lido é um arquivo cujo valor real ninguém
    confere de olho.
    """
    valores = {}
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#"):
            continue
        assert "=" in linha, f"{caminho.name}: linha sem '=': {linha!r}"
        chave, _, valor = linha.partition("=")
        assert '"' not in valor and "'" not in valor, (
            f"{caminho.name}: valor com aspas sai do subconjunto lido aqui: {linha!r}"
        )
        valores[chave] = valor
    return valores


@pytest.mark.parametrize("rodada", sorted(ESPERADO))
def test_o_env_da_rodada_produz_a_regra_que_ele_promete(rodada, monkeypatch):
    """Carregado no `Settings` de verdade. Nome errado de variável cai aqui.

    E é o único lugar onde cai: nome errado não levanta nada, ele só não é
    encontrado — a rodada sairia BASE com o rótulo da regra.
    """
    for chave, valor in _ler_env(RODADAS / f"{rodada}.env").items():
        monkeypatch.setenv(chave, valor)

    settings = Settings()

    for campo, esperado in ESPERADO[rodada].items():
        assert getattr(settings, campo) == esperado, (
            f"{rodada}.env: {campo} saiu {getattr(settings, campo)!r}, "
            f"esperado {esperado!r}"
        )


@pytest.mark.parametrize("rodada", sorted(ESPERADO))
def test_a_rodada_escreve_as_TRES_regras_inclusive_as_desligadas(rodada):
    """Regra ausente por decisão não pode parecer regra ausente por esquecimento.

    Os três nomes aparecem nos quatro arquivos, e é por isso que a string
    vazia precisou virar "desligada" no `Settings`: sem uma grafia para
    desligado, a única forma de dizer "esta não" seria omitir a linha.
    """
    escritas = set(_ler_env(RODADAS / f"{rodada}.env"))
    faltando = {
        "PULSEARB_" + campo.upper() for campo in ESPERADO[rodada]
    } - escritas

    assert not faltando, f"{rodada}.env não escreve: {sorted(faltando)}"


@pytest.mark.parametrize("rodada", sorted(ESPERADO))
def test_nenhuma_rodada_liga_mais_de_uma_regra(rodada, monkeypatch):
    """Duas ligadas não se distinguem — e a rodada não mede nenhuma das duas."""
    for chave, valor in _ler_env(RODADAS / f"{rodada}.env").items():
        monkeypatch.setenv(chave, valor)
    settings = Settings()

    ligadas = [
        nome for nome, ligada in (
            ("recolher", settings.maker_recolhe_quando_o_livro_anda is True),
            ("ancora", settings.maker_ticks_abaixo_do_microprice is not None),
            ("pausa", settings.maker_pausa_apos_fill_toxico_s is not None),
        ) if ligada
    ]

    assert len(ligadas) <= 1, f"{rodada}.env liga {ligadas}"


def test_a_base_nao_liga_nenhuma_e_as_outras_tres_ligam_uma_cada():
    """Sem base, o líquido das outras três não se compara com nada.

    E comparar com uma rodada de outra semana mede a semana, não a regra —
    que é a razão de as quatro correrem juntas.
    """
    assert all(v is None or v is False for v in ESPERADO["base"].values())
    assert {"recolher", "ancora", "pausa"} <= set(ESPERADO)


class TestAUnitTemplate:
    def test_as_tres_coisas_proprias_da_instancia_usam_o_nome_dela(self):
        """Diário, registro e arquivo de regra têm de trazer `%i`.

        Um `%i` esquecido no diário faz as quatro somarem no mesmo arquivo;
        esquecido no `EnvironmentFile`, as quatro rodam a mesma regra. O
        registro de risco saiu daqui para os arquivos das rodadas (na unit
        ele era `Environment=`, que qualquer `.env` de máquina vencia) e tem
        teste próprio em `test_cada_rodada_tem_registro_de_risco_PROPRIO`.
        """
        texto = UNIT.read_text(encoding="utf-8")

        for trecho in (
            "--diario data/diarios/shadow-maker-%i.jsonl",
            "EnvironmentFile=/opt/pulsearb/deploy/rodadas/%i.env",
        ):
            assert trecho in texto, f"a unit não traz: {trecho}"

    def test_o_arquivo_de_regra_NAO_e_opcional(self):
        """`EnvironmentFile=-...` faria a rodada cair para BASE em silêncio.

        Quatro instâncias sem o arquivo subiriam todas verdes, por 14 dias,
        medindo a mesma coisa. O `-` só vale para o `.env` de segredos, que é
        opcional de verdade.
        """
        texto = UNIT.read_text(encoding="utf-8")

        assert "EnvironmentFile=-/opt/pulsearb/deploy/rodadas/" not in texto
        assert "EnvironmentFile=-/opt/pulsearb/.env" in texto

    def test_a_unit_nao_pede_confirmacao_de_LIVE(self):
        """O ensaio é SHADOW. A trava tripla do 3.4 não se afrouxa para medir.

        Confere as DIRETIVAS, não o texto: a unit menciona
        `PULSEARB_CONFIRM_LIVE` num comentário justamente para dizer que ela
        não está lá, e um teste que casasse o texto inteiro proibiria
        explicar a decisão.

        `PULSEARB_MODE=SHADOW` não está mais na unit — mora no perfil
        versionado, porque na unit um `.env` de máquina o venceria.
        """
        diretivas = [
            linha.strip()
            for linha in UNIT.read_text(encoding="utf-8").splitlines()
            if linha.strip() and not linha.strip().startswith("#")
        ]

        perfil = _ler_env(RODADAS / f"{COMUM}.env")

        assert perfil.get("PULSEARB_MODE") == "SHADOW"
        assert "PULSEARB_CONFIRM_LIVE" not in perfil
        for linha in diretivas:
            assert "PULSEARB_CONFIRM_LIVE" not in linha, linha
            assert "EU ACEITO O RISCO" not in linha, linha
            assert "PULSEARB_MODE=LIVE" not in linha, linha
        for rodada in ESPERADO:
            valores = _ler_env(RODADAS / f"{rodada}.env")

            assert "PULSEARB_CONFIRM_LIVE" not in valores, rodada
            assert valores.get("PULSEARB_MODE") in (None, "SHADOW"), rodada

    def test_o_arquivo_da_rodada_e_o_ULTIMO_EnvironmentFile(self):
        """A ordem é a regra, e ela é invisível lendo o arquivo de cima.

        `systemd.exec(5)`: entre `EnvironmentFile=`, o ÚLTIMO vence — e
        qualquer `EnvironmentFile=` vence as linhas `Environment=`. Com o
        `.env` da máquina por último, um `.env` que definisse qualquer
        variável do ensaio (uma regra do maker, o caminho do registro, um
        teto de stake) silenciaria o arquivo versionado nas QUATRO
        instâncias, por 14 dias, sem nada denunciar.

        Achado na revisão do Codex (#131): eu tinha posto o `.env` depois.
        """
        arquivos = [
            linha.strip()
            for linha in UNIT.read_text(encoding="utf-8").splitlines()
            if linha.strip().startswith("EnvironmentFile=")
        ]

        assert arquivos, "a unit não carrega EnvironmentFile nenhum"
        assert arquivos[-1] == "EnvironmentFile=/opt/pulsearb/deploy/rodadas/%i.env", (
            f"o último EnvironmentFile é {arquivos[-1]!r} — o perfil da rodada "
            "tem de ser a palavra final"
        )

    def test_o_env_da_maquina_vem_antes_e_e_o_unico_opcional(self):
        arquivos = [
            linha.strip()
            for linha in UNIT.read_text(encoding="utf-8").splitlines()
            if linha.strip().startswith("EnvironmentFile=")
        ]
        opcionais = [a for a in arquivos if a.startswith("EnvironmentFile=-")]

        assert opcionais == ["EnvironmentFile=-/opt/pulsearb/.env"]
        assert arquivos.index(opcionais[0]) < len(arquivos) - 1

    def test_toda_rodada_declarada_tem_arquivo(self):
        for rodada in ESPERADO:
            assert (RODADAS / f"{rodada}.env").is_file(), rodada

    def test_o_perfil_comum_vem_DEPOIS_do_env_da_maquina(self):
        """`EnvironmentFile` vence `Environment=` sempre, não por ordem.

        Enquanto o perfil morava em linhas `Environment=` da unit, um `.env`
        de máquina com um teto de risco, o `TOP_DE_POOLS` ou até o
        `PULSEARB_MODE` o vencia nas quatro instâncias, por 14 dias. A ordem
        aqui é: segredo, perfil versionado, rodada.
        """
        arquivos = [
            linha.strip()
            for linha in UNIT.read_text(encoding="utf-8").splitlines()
            if linha.strip().startswith("EnvironmentFile=")
        ]

        assert arquivos == [
            "EnvironmentFile=-/opt/pulsearb/.env",
            f"EnvironmentFile=/opt/pulsearb/deploy/rodadas/{COMUM}.env",
            "EnvironmentFile=/opt/pulsearb/deploy/rodadas/%i.env",
        ]

    def test_a_unit_nao_define_perfil_em_Environment(self):
        """Só `Environment=` nenhum resolve — qualquer linha aqui é
        silenciável por um `.env` de máquina."""
        diretivas = [
            linha.strip()
            for linha in UNIT.read_text(encoding="utf-8").splitlines()
            if linha.strip().startswith("Environment=")
        ]

        assert diretivas == [], f"perfil ainda na unit: {diretivas}"

    def test_o_perfil_comum_produz_o_ensaio_das_r4_r8(self, monkeypatch):
        for chave, valor in _ler_env(RODADAS / f"{COMUM}.env").items():
            monkeypatch.setenv(chave, valor)

        settings = Settings()

        for campo, esperado in PERFIL.items():
            assert getattr(settings, campo) == esperado, campo
        assert settings.mode.value == "SHADOW"

    def test_o_perfil_comum_NAO_liga_regra_experimental(self):
        """Se ligasse, ligaria nas quatro e nenhuma mediria a sua."""
        escritas = set(_ler_env(RODADAS / f"{COMUM}.env"))

        assert not [
            c for c in escritas if c.startswith("PULSEARB_MAKER_")
        ], "o perfil comum não escreve regra — regra é da rodada"

    def test_cada_rodada_tem_registro_de_risco_PROPRIO(self):
        """Na unit ele era `Environment=` e um `.env` daria o mesmo a todas.

        E registro compartilhado não é arrumação: o `_gravar` do portão monta
        o `.tmp` a partir deste caminho, então duas rodadas no mesmo registro
        escrevem o mesmo temporário e o rename atômico pode publicar uma
        mistura das duas.
        """
        caminhos = {}
        for rodada in ESPERADO:
            valores = _ler_env(RODADAS / f"{rodada}.env")
            chave = "PULSEARB_RISK__CAMINHO_DO_REGISTRO"

            assert chave in valores, f"{rodada}.env não define o registro"
            caminhos[rodada] = valores[chave]

        assert len(set(caminhos.values())) == len(ESPERADO), caminhos

    def test_todo_arquivo_de_rodada_esta_declarado(self):
        """Um `.env` a mais na pasta é uma rodada que ninguém confere.

        Ela subiria com `systemctl enable pulsearb-shadow-maker@qualquer` e
        nenhum teste diria que regra ela liga.
        """
        na_pasta = {p.stem for p in RODADAS.glob("*.env")} - {COMUM}

        assert na_pasta == set(ESPERADO), (
            f"na pasta e não declaradas: {sorted(na_pasta - set(ESPERADO))}; "
            f"declaradas e sem arquivo: {sorted(set(ESPERADO) - na_pasta)}"
        )


class TestVazioEDesligado:
    """`VAR=` numa unit quer dizer desligado — e derrubava o processo."""

    def test_variavel_vazia_e_desligada_e_nao_erro(self, monkeypatch):
        monkeypatch.setenv("PULSEARB_MAKER_TICKS_ABAIXO_DO_MICROPRICE", "")
        monkeypatch.setenv("PULSEARB_MAKER_PAUSA_APOS_FILL_TOXICO_S", "")

        settings = Settings()

        assert settings.maker_ticks_abaixo_do_microprice is None
        assert settings.maker_pausa_apos_fill_toxico_s is None

    def test_so_espaco_tambem_e_desligado(self, monkeypatch):
        """`VAR= ` no arquivo chega como `" "`. Recusar por um espaço seria
        recusar pelo motivo errado."""
        monkeypatch.setenv("PULSEARB_MAKER_TICKS_ABAIXO_DO_MICROPRICE", "  ")

        assert Settings().maker_ticks_abaixo_do_microprice is None

    def test_ZERO_continua_LIGADO_nos_dois(self, monkeypatch):
        """É a razão de "desligado" precisar de grafia própria.

        Zero é um valor válido e significativo nos dois knobs — âncora no
        microprice exato, pausa de duração zero —, então ele não podia ser
        emprestado para dizer "desligado".
        """
        monkeypatch.setenv("PULSEARB_MAKER_TICKS_ABAIXO_DO_MICROPRICE", "0")
        monkeypatch.setenv("PULSEARB_MAKER_PAUSA_APOS_FILL_TOXICO_S", "0")

        settings = Settings()

        assert settings.maker_ticks_abaixo_do_microprice == 0
        assert settings.maker_pausa_apos_fill_toxico_s == 0.0

    def test_valor_negativo_segue_RECUSADO(self, monkeypatch):
        """A validação no carregamento é o que impede a rota morrer em silêncio
        por 14 dias com o processo vivo — o vazio não pode ter aberto essa porta."""
        monkeypatch.setenv("PULSEARB_MAKER_TICKS_ABAIXO_DO_MICROPRICE", "-1")

        with pytest.raises(ValueError):
            Settings()

    def test_texto_que_nao_e_numero_segue_RECUSADO(self, monkeypatch):
        """Vazio é desligado; `nao` é engano de quem escreveu, e tem de falhar."""
        monkeypatch.setenv("PULSEARB_MAKER_PAUSA_APOS_FILL_TOXICO_S", "nao")

        with pytest.raises(ValueError):
            Settings()


class TestOPrazoEAbsoluto:
    """`--duration 14d` reexecutado pelo restart dá outros 14 dias.

    Uma instância que caísse no dia 13 observaria 27 dias e terminaria 13
    dias depois das irmãs: os rewards e o markout dela deixariam de cobrir o
    MESMO intervalo de mercado que as quatro existem para comparar (revisão
    do Codex, #131). O instante absoluto sobrevive ao restart sem persistir
    nada, e escrito no perfil comum faz as quatro terminarem juntas por
    construção.
    """

    def test_a_unit_usa_prazo_ABSOLUTO_e_nao_duration(self):
        texto = UNIT.read_text(encoding="utf-8")
        execstart = next(
            linha for linha in texto.splitlines()
            if linha.startswith("ExecStart=")
        )

        assert "--ate ${PULSEARB_RODADA_TERMINA_EM}" in execstart
        assert "--duration" not in execstart

    def test_o_prazo_mora_no_perfil_COMUM_e_nao_na_rodada(self):
        """No `%i.env` cada rodada teria o seu, e elas desalinhariam."""
        assert "PULSEARB_RODADA_TERMINA_EM" in _ler_env(RODADAS / f"{COMUM}.env")
        for rodada in ESPERADO:
            assert "PULSEARB_RODADA_TERMINA_EM" not in _ler_env(
                RODADAS / f"{rodada}.env"
            ), rodada

    def test_o_prazo_nasce_VAZIO_para_o_operador_escrever(self):
        """Um instante versionado seria um prazo que venceu no passado e
        ninguém notaria; vazio a unit não sobe, que é o que se quer."""
        assert _ler_env(RODADAS / f"{COMUM}.env")["PULSEARB_RODADA_TERMINA_EM"] == ""

    def test_saida_2_NAO_reergue(self):
        """`--ate` vazio sai com 2, e reerguer seria laço de restart eterno.

        `StartLimitIntervalSec=0` desliga o limite de partidas, então nada
        mais seguraria. Saída 1 (rodada que caiu) continua reerguendo.
        """
        for unit in (
            RAIZ / "deploy" / "pulsearb-shadow-maker.service",
            UNIT,
        ):
            diretivas = [
                linha.strip()
                for linha in unit.read_text(encoding="utf-8").splitlines()
                if linha.strip().startswith(("Restart=", "RestartPreventExitStatus="))
            ]

            assert "RestartPreventExitStatus=2" in diretivas, unit.name
            assert "Restart=on-failure" in diretivas, unit.name


class TestSegundosAte:
    """A conversão do instante absoluto, e o que ela recusa."""

    def test_instante_futuro_vira_os_segundos_que_faltam(self):
        import time as _time

        from pulsearb.live.shadow import segundos_ate

        daqui = datetime.fromtimestamp(_time.time() + 3600, tz=UTC)

        assert segundos_ate(daqui.isoformat()) == pytest.approx(3600, abs=5)

    def test_o_Z_do_RFC3339_e_aceito(self):
        """É a forma que o runbook escreve (`date -u ... +%Y-%m-%dT%H:%M:%SZ`)."""
        from pulsearb.live.shadow import segundos_ate

        assert isinstance(segundos_ate("2099-01-01T00:00:00Z"), float)

    def test_instante_SEM_FUSO_e_recusado(self):
        """Sem fuso seria lido como hora local da VPS, e a rodada acabaria na
        hora errada em silêncio — que é pior que não subir."""
        from pulsearb.live.shadow import segundos_ate

        with pytest.raises(ValueError, match="sem fuso"):
            segundos_ate("2099-01-01T00:00:00")

    def test_texto_que_nao_e_instante_e_recusado(self):
        from pulsearb.live.shadow import segundos_ate

        with pytest.raises(ValueError, match="RFC3339"):
            segundos_ate("14d")

    def test_instante_JA_VENCIDO_da_negativo_e_nao_erro(self):
        """Prazo vencido é rodada TERMINADA: um restart depois do fim tem de
        encerrar de novo, e com saída 0, senão o `on-failure` reergue para
        sempre."""
        from pulsearb.live.shadow import segundos_ate

        assert segundos_ate("2000-01-01T00:00:00Z") < 0
