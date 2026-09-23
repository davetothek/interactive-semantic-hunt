# Bash completion for ish.
#
# Finish a filter word on the command line the way the picker does:
# `ish ty<Tab>` becomes `ish type:`, `ish lang:cp<Tab>` becomes
# `ish lang:cpp`, and a word with several answers is listed.
# `ish-complete` does the work, so the shell learns nothing about the
# registered languages.
#
# Source this file from ~/.bashrc, or copy it to the directory
# bash-completion reads, and it binds itself to `ish`.

_ish_query_word() {
  local cur=$1
  local grown listed
  grown=$(ish-complete "$cur" 2>/dev/null) || return 1
  listed=$(ish-complete --candidates "$cur" 2>/dev/null)

  # A finished value ends in a space, which says the word is done. Let
  # readline add the space, so the word itself stays clean.
  if [[ $grown == *' ' ]]; then
    compopt +o nospace 2>/dev/null
    grown=${grown% }
  fi

  local replies=()
  if [ -z "$listed" ]; then
    # One answer, or none. Either way `ish-complete` says what the word
    # becomes, and a word it leaves alone completes to itself.
    replies=("$grown")
  else
    # Several answers. Name each in full, so readline lists them and
    # grows the word to what they share. Values are listed without
    # their key, so put it back.
    local prefix=""
    case $cur in *:*) prefix="${cur%%:*}:" ;; esac
    listed=${listed%%  ...*}
    local choice
    for choice in $listed; do
      replies+=("$prefix$choice")
    done
  fi

  # Readline breaks a word at a colon, so it replaces only what follows
  # the last one. Give it only that much.
  if [[ $cur == *:* && $COMP_WORDBREAKS == *:* ]]; then
    local head=${cur%:*}:
    local reply
    COMPREPLY=()
    for reply in "${replies[@]}"; do
      COMPREPLY+=("${reply#"$head"}")
    done
  else
    COMPREPLY=("${replies[@]}")
  fi
}

_ish() {
  # Take the word from the line itself. COMP_WORDS has already been
  # broken at every colon, and a filter word is `key:value`.
  local line=${COMP_LINE:0:$COMP_POINT}
  local cur=${line##* }

  case $cur in
    -*)
      local flags
      flags=$(ish --help 2>/dev/null | grep -oE -- '(^|[ ,[])--?[a-z][a-z-]*' | tr -d ' ,[' | sort -u)
      COMPREPLY=($(compgen -W "$flags" -- "$cur"))
      return
      ;;
  esac

  _ish_query_word "$cur" || COMPREPLY=()
}

complete -o nospace -F _ish ish
