#!/usr/bin/env bash
# _wrapper.sh — ponte entre os hooks do git e o git-revisor.
# Roda o revisor python primeiro e depois o hook local do repositório, se houver.
set -u

nome="$(basename "$0")"
revisor="$(dirname "$(readlink -f "$0")")/../git_revisor.py"
# Hook local é procurado no git-common-dir (estado compartilhado entre worktrees);
# --git-dir apontaria para .git/worktrees/<x> num worktree secundário, e
# --git-path hooks respeitaria core.hooksPath, devolvendo o próprio wrapper.
comum="$(git rev-parse --git-common-dir 2>/dev/null)"
hook_local=""
if [ -n "$comum" ]; then
  case "$comum" in
    /*) ;;
    *) comum="$(pwd)/$comum" ;;
  esac
  candidato="$comum/hooks/$nome"
  # hook local que resolve para o próprio wrapper é ignorado (evita loop)
  if [ -x "$candidato" ] && [ "$(readlink -f "$candidato")" != "$(readlink -f "$0")" ]; then
    hook_local="$candidato"
  fi
fi

tem_revisor() { command -v python3 >/dev/null 2>&1 && [ -f "$revisor" ]; }
tem_local() { [ -n "$hook_local" ] && [ -x "$hook_local" ]; }
avisa() { echo "revisor: aviso: python3 ou $revisor ausente; seguindo sem revisão" >&2; }
# pre-push falha fechado: sem revisor não há como aprovar o push
instalacao_quebrada() {
  echo "revisor: instalação quebrada (python3/git_revisor.py ausente); push bloqueado." >&2
  echo "Para enviar mesmo assim: REVISOR_PULAR=1 git push" >&2
  exit 1
}

case "$nome" in
  pre-push)
    entrada="$(cat)"
    # $(cat) come o \n final: repõe ao repassar; entrada vazia não imprime nada.
    repassar() { [ -n "$entrada" ] && printf '%s\n' "$entrada"; }
    if tem_revisor; then
      repassar | python3 "$revisor" hook pre-push "$@" || exit $?
    elif [ "${REVISOR_PULAR:-}" = "1" ]; then
      avisa
    else
      instalacao_quebrada
    fi
    if tem_local; then
      repassar | "$hook_local" "$@"
      exit $?
    fi
    exit 0
    ;;
  pre-commit|post-commit)
    if tem_revisor; then
      python3 "$revisor" hook "$nome" "$@"
      codigo=$?
      if [ "$nome" = "pre-commit" ] && [ "$codigo" -ne 0 ]; then
        exit "$codigo"
      fi
    else
      avisa
    fi
    if tem_local; then
      exec "$hook_local" "$@"
    fi
    exit 0
    ;;
  *)
    if tem_local; then
      exec "$hook_local" "$@"
    fi
    exit 0
    ;;
esac
