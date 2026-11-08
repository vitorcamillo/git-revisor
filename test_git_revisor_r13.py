"""Testes R13: timeout de check não deixa processo órfão.

No timeout, o GRUPO inteiro do check tem de morrer (SIGTERM, depois
SIGKILL). `subprocess.run` matava só o filho direto: com
`npm test → sh → node jest`, o npm morria e o jest ficava órfão no
systemd a 98% de CPU para sempre. Hoje qualquer caminho de check
(com ou sem `abortar`) passa pelo mesmo polling que mata o grupo.
"""
from __future__ import annotations

import importlib.util
import os
import time
from pathlib import Path

_ARQUIVO = Path(__file__).with_name("git_revisor.py")
_spec = importlib.util.spec_from_file_location("git_revisor", _ARQUIVO)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def test_timeout_mata_o_grupo_nao_so_o_filho(tmp_path: Path) -> None:
    """Check que lança um neto (`sleep 300 &`) morre inteiro no timeout.

    `sh -c 'sleep 300 & echo $! > <pid>; wait'` com timeout de 2 s:
    `_executar_checks` devolve falha por timeout e, em até 15 s, o pid do
    neto não existe mais. Sem a correção (`subprocess.run` matando só o
    filho direto), o `sleep` ficava órfão vivo e o teste falhava no
    prazo de 15 s.
    """
    pid_neto = tmp_path / "neto.pid"
    checks = [("", ["sh", "-c", f"sleep 300 & echo $! > {pid_neto}; wait"],
               "", True)]
    inicio = time.monotonic()
    ok, rel = mod._executar_checks(checks, str(tmp_path), 2)
    dur = time.monotonic() - inicio
    assert ok is False
    assert "exit timeout" in rel
    assert pid_neto.exists(), "check não chegou a criar o neto antes do timeout"
    pid = int(pid_neto.read_text(encoding="utf-8").strip())
    prazo = time.monotonic() + 15
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        else:
            assert time.monotonic() < prazo, (
                f"neto {pid} ainda vivo {dur:.1f}s após o timeout (órfão)")
            time.sleep(0.2)


def test_check_rapido_sem_timeout_continua_aprovando(tmp_path: Path) -> None:
    """Sem timeout, o caminho novo (Popen + polling) aprova como antes."""
    checks = [("", ["sh", "-c", "echo ok"], "", True)]
    ok, rel = mod._executar_checks(checks, str(tmp_path), 60)
    assert ok is True and rel == ""
