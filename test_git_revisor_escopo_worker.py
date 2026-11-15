"""Guarda de escopo do corretor: mudança do worker em outro repo é registrada."""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

_spec = importlib.util.spec_from_file_location("git_revisor", Path(__file__).with_name("git_revisor.py"))
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def _repo(tmp_path: Path, nome: str = "outro") -> Path:
    outro = tmp_path / nome
    outro.mkdir()
    for args in (["init", "-q"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "i"]):
        subprocess.run(["git", *args], cwd=outro, check=True)
    (outro / "a.txt").write_text("a\n")
    (outro / "b.txt").write_text("b\n")
    (outro / "c.txt").write_text("c\n")
    subprocess.run(["git", "add", "."], cwd=outro, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "abc"], cwd=outro, check=True)
    return outro


def _avisos(monkeypatch) -> list[tuple[str, str]]:
    avisos: list[tuple[str, str]] = []
    monkeypatch.setattr(mod, "notificar", lambda titulo, msg: avisos.append((titulo, msg)))
    return avisos


def _patches(tmp_path: Path) -> list[Path]:
    return list((tmp_path / "state" / "git-revisor" / "fora-do-escopo").glob("*-outro.patch"))


def test_worker_fora_do_escopo_preservado_e_avisado(tmp_path, monkeypatch) -> None:
    outro = _repo(tmp_path)
    (outro / "b.txt").write_text("edição do usuário\n")  # já sujo antes: intocado
    avisos = _avisos(monkeypatch)
    monkeypatch.setattr(mod, "_repos_vigiados", lambda cwd: [str(outro)])
    monkeypatch.setattr(mod, "_rodar_cadeia", lambda t, c: (outro / "a.txt").write_text("worker\n") and "glm")
    assert mod.rodar_worker("tarefa", str(tmp_path)) == "glm"
    # repo vigiado é onde o usuário trabalha: nada é desfeito às cegas
    assert (outro / "a.txt").read_text() == "worker\n"
    assert (outro / "b.txt").read_text() == "edição do usuário\n"
    patches = _patches(tmp_path)
    assert len(patches) == 1 and "+worker" in patches[0].read_text()
    assert avisos and "a.txt" in avisos[0][1]


def test_worker_arquivo_novo_movido_para_fora_do_escopo(tmp_path, monkeypatch) -> None:
    outro = _repo(tmp_path)
    avisos = _avisos(monkeypatch)
    monkeypatch.setattr(mod, "_repos_vigiados", lambda cwd: [str(outro)])

    def cadeia(t, c):
        (outro / "novo_modulo.py").write_text("novo\n")
        return "glm"

    monkeypatch.setattr(mod, "_rodar_cadeia", cadeia)
    assert mod.rodar_worker("tarefa", str(tmp_path)) == "glm"
    assert not (outro / "novo_modulo.py").exists()
    movidos = list((tmp_path / "state" / "git-revisor" / "fora-do-escopo").rglob("novo_modulo.py"))
    assert len(movidos) == 1 and movidos[0].read_text() == "novo\n"
    assert avisos


def test_edicao_concorrente_de_terceiro_preservada(tmp_path, monkeypatch) -> None:
    outro = _repo(tmp_path)
    avisos = _avisos(monkeypatch)
    monkeypatch.setattr(mod, "_repos_vigiados", lambda cwd: [str(outro)])
    # c.txt estava limpo na foto inicial; a escrita abaixo simula o usuário
    # editando o repo principal enquanto a correção roda em fundo.
    monkeypatch.setattr(mod, "_rodar_cadeia", lambda t, c: (outro / "c.txt").write_text("concorrente\n") and "glm")
    assert mod.rodar_worker("tarefa", str(tmp_path)) == "glm"
    assert (outro / "c.txt").read_text() == "concorrente\n"
    assert avisos and "c.txt" in avisos[0][1]


def test_arquivo_ja_sujo_reeditado_pelo_worker_detectado(tmp_path, monkeypatch) -> None:
    outro = _repo(tmp_path)
    (outro / "b.txt").write_text("edição do usuário\n")  # já sujo antes da foto
    avisos = _avisos(monkeypatch)
    monkeypatch.setattr(mod, "_repos_vigiados", lambda cwd: [str(outro)])
    monkeypatch.setattr(mod, "_rodar_cadeia", lambda t, c: (outro / "b.txt").write_text("worker sobre sujo\n") and "glm")
    assert mod.rodar_worker("tarefa", str(tmp_path)) == "glm"
    assert (outro / "b.txt").read_text() == "worker sobre sujo\n"
    patches = _patches(tmp_path)
    assert len(patches) == 1 and "+worker sobre sujo" in patches[0].read_text()
    assert avisos and "b.txt" in avisos[0][1]


def test_nome_com_espaco_e_acento_detectado(tmp_path, monkeypatch) -> None:
    outro = _repo(tmp_path)
    nome = "arquivo com espaço ç.txt"
    (outro / nome).write_text("original\n")
    subprocess.run(["git", "add", "."], cwd=outro, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "nome"], cwd=outro, check=True)
    avisos = _avisos(monkeypatch)
    monkeypatch.setattr(mod, "_repos_vigiados", lambda cwd: [str(outro)])
    monkeypatch.setattr(mod, "_rodar_cadeia", lambda t, c: (outro / nome).write_text("worker\n") and "glm")
    assert mod.rodar_worker("tarefa", str(tmp_path)) == "glm"
    assert (outro / nome).read_text() == "worker\n"
    patches = _patches(tmp_path)
    assert len(patches) == 1 and "+worker" in patches[0].read_text()
    assert avisos and nome in avisos[0][1]
