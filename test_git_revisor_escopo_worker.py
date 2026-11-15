"""Guarda de escopo do corretor: mudança do worker em outro repo é desfeita."""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

_spec = importlib.util.spec_from_file_location("git_revisor", Path(__file__).with_name("git_revisor.py"))
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def test_worker_fora_do_escopo_desfeito(tmp_path, monkeypatch) -> None:
    outro = tmp_path / "outro"
    outro.mkdir()
    for args in (["init", "-q"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "i"]):
        subprocess.run(["git", *args], cwd=outro, check=True)
    (outro / "a.txt").write_text("a\n")
    (outro / "b.txt").write_text("b\n")
    subprocess.run(["git", "add", "."], cwd=outro, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "ab"], cwd=outro, check=True)
    (outro / "b.txt").write_text("edição do usuário\n")  # já sujo antes: intocado
    monkeypatch.setattr(mod, "_repos_vigiados", lambda cwd: [str(outro)])
    monkeypatch.setattr(mod, "_rodar_cadeia", lambda t, c: (outro / "a.txt").write_text("worker\n") and "glm")
    assert mod.rodar_worker("tarefa", str(tmp_path)) == "glm"
    assert (outro / "a.txt").read_text() == "a\n"
    assert (outro / "b.txt").read_text() == "edição do usuário\n"
    patches = list((tmp_path / "state" / "git-revisor" / "fora-do-escopo").glob("*-outro.patch"))
    assert len(patches) == 1 and "+worker" in patches[0].read_text()
