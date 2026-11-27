"""Testes Spec A: ordem configurável da cadeia de correção.

Cobre `ordem_correcao` (padrão, ordem válida, inválidos) e `_rodar_cadeia`
(respeito à ordem), sem rede nem wrappers reais.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_spec = importlib.util.spec_from_file_location("git_revisor", Path(__file__).parent.parent / "git_revisor.py")
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

PADRAO = ("glm", "muse", "codex", "claude")


def _escrever_ordem(dir_config: Path, conteudo: str) -> None:
    pasta = dir_config / "git-revisor"
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / "ordem.json").write_text(conteudo, encoding="utf-8")


def test_sem_arquivo_devolve_padrao(tmp_path, monkeypatch) -> None:
    """Sem ordem.json → devolve PADRAO."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert mod.ordem_correcao(PADRAO) == PADRAO


def test_ordem_valida(tmp_path, monkeypatch) -> None:
    """Permutação exata → devolve a tupla do arquivo."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    _escrever_ordem(tmp_path, json.dumps({"correcao": ["claude", "codex", "glm", "muse"]}))
    assert mod.ordem_correcao(PADRAO) == ("claude", "codex", "glm", "muse")


def test_ordens_invalidas_caem_no_padrao(tmp_path, monkeypatch) -> None:
    """Cada caso inválido num diretório de config próprio → PADRAO."""
    casos = [
        json.dumps({"correcao": []}),
        json.dumps({"correcao": ["glm", "muse", "codex"]}),
        json.dumps({"correcao": ["glm", "glm", "codex", "claude"]}),
        json.dumps({"correcao": ["glm", "muse", "codex", "claude", "extra"]}),
        json.dumps({"correcao": ["glm", "muse", "codex", 123]}),
        json.dumps({"correcao": "glm"}),
        "{quebrado",
    ]
    for i, conteudo in enumerate(casos):
        dir_config = tmp_path / f"cfg{i}"
        dir_config.mkdir()
        monkeypatch.setenv("XDG_CONFIG_HOME", str(dir_config))
        _escrever_ordem(dir_config, conteudo)
        assert mod.ordem_correcao(PADRAO) == PADRAO, f"caso {i}: {conteudo!r}"


def test_rodar_cadeia_respeita_ordem(tmp_path, monkeypatch) -> None:
    """Ordem claude→codex→glm→muse: falha claude, ok codex → para em codex."""
    dir_config = tmp_path / "config"
    dir_estado = tmp_path / "estado"
    dir_config.mkdir()
    dir_estado.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(dir_config))
    monkeypatch.setenv("XDG_STATE_HOME", str(dir_estado))
    _escrever_ordem(dir_config, json.dumps({"correcao": ["claude", "codex", "glm", "muse"]}))
    for env in ("REVISOR_CMD_WORKER", "REVISOR_CMD_WORKER_MUSE",
                "REVISOR_CMD_WORKER2", "REVISOR_CMD_WORKER3"):
        monkeypatch.delenv(env, raising=False)
    chamados: list[str] = []

    def fake(cmd, cwd, args, limite, stderr_out=None):
        chamados.append(cmd[0])
        if cmd[0] == "codex-ask":
            return 0, "ok"
        return 1, "falhou"

    monkeypatch.setattr(mod, "chamar_comando", fake)
    assert mod._rodar_cadeia("tarefa-teste", str(tmp_path)) == "codex"
    assert chamados == ["claude-ask", "codex-ask"]
