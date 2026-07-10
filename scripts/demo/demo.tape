# VHS tape for the README demo GIF.
# Render:  cd scripts/demo && PATH="$(git rev-parse --show-toplevel)/.venv/bin:$PATH" vhs demo.tape
# Needs: vhs (brew install vhs), and `brain` on PATH.

Output demo.gif

Set Shell "bash"
Set FontSize 22
Set Width 1150
Set Height 640
Set Padding 20
Set Theme "Catppuccin Macchiato"
Set TypingSpeed 35ms

Hide
Type "export DEMO=$(mktemp -d) && cp seed.sh $DEMO/ && cd $DEMO && clear"
Enter
Sleep 1s
Show

Type "# brainkit — a company brain where privacy is compiled in"
Enter
Sleep 2s

Type "brain init master --company 'Acme Co'"
Enter
Sleep 2.5s

Type "./seed.sh master"
Enter
Sleep 2.5s

Type "# Bob keeps a PRIVATE note in his space:"
Enter
Type "cat master/People/bob/Notes/salary-negotiation.md"
Enter
Sleep 3.5s

Type "# compile: every person gets a vault with ONLY what they may read"
Enter
Type "brain compile --master master --out compiled"
Enter
Sleep 3s

Type "find compiled/alice -name '*.md'"
Enter
Sleep 4s

Type "# the guarantee — Bob's note was never copied into Alice's vault:"
Enter
Type "grep -ri salary compiled/alice ; echo 'no matches. nothing to leak.'"
Enter
Sleep 4s

Type "brain doctor --master master --out compiled"
Enter
Sleep 3s

Type "# privacy by construction — github.com/joedanz/brainkit"
Enter
Sleep 4.5s
