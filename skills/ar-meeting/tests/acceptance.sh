#!/bin/bash
# ar-meeting acceptance suite. Runs against the installed skill.
# Needs no pip packages. LibreOffice/poppler are optional — the pptx fidelity
# test is skipped (not failed) when they are absent.
SKILL=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
LM="python3 $SKILL/scripts/labmeet.py"
CV="python3 $SKILL/scripts/convert.py"
W=$(mktemp -d /tmp/lmacc.XXXX); cd "$W" || exit 1
PASS=0; FAIL=0; SKIP=0
ok(){   echo "  PASS  $1"; PASS=$((PASS+1)); }
no(){   echo "  FAIL  $1"; FAIL=$((FAIL+1)); }
skip(){ echo "  SKIP  $1"; SKIP=$((SKIP+1)); }
chk(){ if [ "$2" = "$3" ]; then ok "$1"; else no "$1 (got '$2', want '$3')"; fi; }

# portable watchdog: stock macOS has no GNU `timeout`
if command -v timeout >/dev/null 2>&1; then
  tmo(){ timeout "$@"; }
elif command -v gtimeout >/dev/null 2>&1; then
  tmo(){ gtimeout "$@"; }
else
  tmo(){ local secs="$1"; shift; "$@" & local p=$!
         ( sleep "$secs"; kill -9 $p 2>/dev/null ) 2>/dev/null & local w=$!
         wait $p 2>/dev/null; local rc=$?; kill $w 2>/dev/null; return $rc; }
fi

# a stub answerer so the auto-answer path is tested without calling a real model
cat > stub-answerer.sh <<'STUB'
#!/bin/bash
out=""; prev=""
for a in "$@"; do [ "$prev" = "-o" ] && out="$a"; prev="$a"; done
prompt=$(cat)
case "$prompt" in *"__SLOW__"*) sleep 30;; esac
{ echo "**stub answer**"; echo "$prompt" | grep -c "New comment:" >/dev/null && echo "saw the comment"; } > "$out"
STUB
chmod +x stub-answerer.sh
export LABMEET_ANSWER_CMD="[\"$W/stub-answerer.sh\"]"
export LABMEET_ANSWER_MODE=codex

cat > deck.md <<'EOF'
# Results
We trained a 12-layer model.

- 3.1 A RMSD
- 40 % faster

## Ablations
Recycling matters.
EOF

echo "[T1] markdown -> deck + slide context"
$CV deck.md --name t1 --out meeting/t1 >/dev/null 2>&1
chk "index.html written" "$([ -f meeting/t1/index.html ] && echo y)" "y"
chk "theme.css copied"   "$([ -f meeting/t1/theme.css ] && echo y)" "y"
chk "2 slides"           "$(grep -c 'data-slide=' meeting/t1/index.html)" "2"
chk "slides.json for the answerer" \
  "$(python3 -c "import json;d=json.load(open('meeting/t1/slides.json'));print(len(d), 'Recycling' in d[1]['text'])")" "2 True"
chk "source identity written" "$(python3 -c "import json;print(json.load(open('meeting/t1/meeting.json'))['source'].endswith('/deck.md'))")" "True"
chk "model picker shipped" "$(grep -c 'lm-composer-model' meeting/t1/index.html)" "6"
chk "resizable margin shipped" "$(grep -c 'lm-margin-resizer' meeting/t1/index.html)" "2"
chk "comment tabs shipped" "$(grep -c 'lm-thread-tabs' meeting/t1/index.html)" "1"
chk "no external refs"   "$(grep -oE 'https?://[^\"]+' meeting/t1/index.html | grep -v 127.0.0.1 | wc -l | tr -d ' ')" "0"

echo "[T2] rendering pipeline + graceful fallback"
  python3 - <<'PY'
import zipfile
P='xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
A='xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
Rn='xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
REL='xmlns="http://schemas.openxmlformats.org/package/2006/relationships"'
sp=('<p:sp><p:nvSpPr><p:cNvPr id="2" name="Title 1"/><p:cNvSpPr/>'
  '<p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>'
  '<p:spPr><a:xfrm><a:off x="914400" y="685800"/><a:ext cx="7315200" cy="1143000"/></a:xfrm></p:spPr>'
  '<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr lang="en-US" sz="4400"/>'
  '<a:t>Fixture Title</a:t></a:r></a:p></p:txBody></p:sp>')
slide=('<p:sld %s %s %s><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/>'
     '<p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/>%s</p:spTree></p:cSld></p:sld>'%(P,A,Rn,sp))
pres=('<p:presentation %s %s %s><p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst>'
    '<p:sldSz cx="9144000" cy="6858000"/></p:presentation>'%(P,A,Rn))
prels=('<Relationships %s><Relationship Id="rId1" Target="slides/slide1.xml" '
     'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"/></Relationships>'%REL)
root=('<Relationships %s><Relationship Id="rId1" Target="ppt/presentation.xml" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"/></Relationships>'%REL)
ct=('<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
  '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
  '<Default Extension="xml" ContentType="application/xml"/></Types>')
with zipfile.ZipFile('fixture.pptx','w') as z:
  z.writestr('[Content_Types].xml',ct); z.writestr('_rels/.rels',root)
  z.writestr('ppt/presentation.xml',pres); z.writestr('ppt/_rels/presentation.xml.rels',prels)
  z.writestr('ppt/slides/slide1.xml',slide)
PY
# this fixture is deliberately too minimal for LibreOffice: auto mode must still
# produce a deck via the built-in renderer instead of hard-failing
$CV fixture.pptx --name t2fb --out meeting/t2fb >/dev/null 2>&1
chk "falls back when the renderer chokes" "$([ -f meeting/t2fb/index.html ] && echo y)" "y"
chk "fallback used the native canvas" "$(grep -c 'class="lm-canvas"' meeting/t2fb/index.html)" "1"
chk "fallback still writes slide text" "$(python3 -c "
import json;print('Fixture Title' in json.load(open('meeting/t2fb/slides.json'))[0]['text'])")" "True"
$CV fixture.pptx --name t2n --mode native --out meeting/t2n >/dev/null 2>&1
chk "explicit --mode native works" "$(grep -c 'class="lm-canvas"' meeting/t2n/index.html)" "1"

if command -v soffice >/dev/null && command -v pdftoppm >/dev/null; then
  printf 'Rendered Slide One\n\fRendered Slide Two\n' > src.txt
  soffice --headless --convert-to pdf --outdir . src.txt >/dev/null 2>&1
  if [ -f src.pdf ]; then
    $CV src.pdf --name t2r --out meeting/t2r >/dev/null 2>&1
    chk "pages rasterised" "$([ -f meeting/t2r/slides/page-1.png ] && echo y)" "y"
    chk "renders above 150 dpi" "$(python3 -c "
import struct
d=open('meeting/t2r/slides/page-1.png','rb').read(33)
print('yes' if struct.unpack('>I', d[16:20])[0] > 1400 else 'no')")" "yes"
    chk "page text kept for the answerer" "$(python3 -c "
import json;print('Rendered Slide One' in json.load(open('meeting/t2r/slides.json'))[0]['text'])")" "True"
  else
    skip "LibreOffice could not build the sample pdf"
  fi
else
  skip "LibreOffice/poppler absent - rasterisation path not exercised"
fi

echo "[T3] comment threads"
$LM open meeting/t1 --no-open >/dev/null 2>&1
PORT=$(python3 -c "import json;print(json.load(open('meeting/t1/.state/server.json'))['port'])")
TID=$(curl -s -X POST "http://127.0.0.1:$PORT/api/threads" -H 'Content-Type: application/json' \
  -d '{"slide":2,"x":40,"y":55,"text":"why does recycling matter?"}' \
  | python3 -c "import json,sys;print(json.load(sys.stdin)['thread'])")
chk "thread created" "$TID" "t1"
for i in $(seq 1 20); do
  ST=$(curl -s "http://127.0.0.1:$PORT/api/threads" | python3 -c "
import json,sys
t=json.load(sys.stdin)['threads'][0]
print(t['messages'][-1]['status'] if len(t['messages'])>1 else 'waiting')")
  [ "$ST" = "done" ] && break; sleep 1
done
chk "auto-answered" "$ST" "done"
chk "answer stored" "$(curl -s "http://127.0.0.1:$PORT/api/threads" | python3 -c "
import json,sys;print('stub answer' in json.load(sys.stdin)['threads'][0]['messages'][1]['text'])")" "True"
chk "anchor preserved" "$(curl -s "http://127.0.0.1:$PORT/api/threads" | python3 -c "
import json,sys;t=json.load(sys.stdin)['threads'][0];print(int(t['x']),int(t['y']),t['slide'])")" "40 55 2"
curl -s -X POST "http://127.0.0.1:$PORT/api/threads/$TID/messages" -H 'Content-Type: application/json' \
  -d '{"text":"follow up question"}' >/dev/null
for i in $(seq 1 20); do
  N=$(curl -s "http://127.0.0.1:$PORT/api/threads" | python3 -c "
import json,sys;print(len(json.load(sys.stdin)['threads'][0]['messages']))")
  [ "$N" = "4" ] && break; sleep 1
done
chk "follow-up keeps one thread" "$N" "4"
chk "still a single thread" "$(curl -s "http://127.0.0.1:$PORT/api/threads" | python3 -c "
import json,sys;print(len(json.load(sys.stdin)['threads']))")" "1"
curl -s -X POST "http://127.0.0.1:$PORT/api/threads/$TID/resolve" -d '{}' >/dev/null
chk "resolve works" "$(curl -s "http://127.0.0.1:$PORT/api/threads" | python3 -c "
import json,sys;print(json.load(sys.stdin)['threads'][0]['resolved'])")" "True"
chk "bad thread id = 404" "$(curl -s -o /dev/null -w '%{http_code}' -X POST \
  "http://127.0.0.1:$PORT/api/threads/nope/messages" -H 'Content-Type: application/json' -d '{"text":"x"}')" "404"
chk "empty comment = 400" "$(curl -s -o /dev/null -w '%{http_code}' -X POST \
  "http://127.0.0.1:$PORT/api/threads" -H 'Content-Type: application/json' -d '{"slide":1,"x":1,"y":1,"text":"  "}')" "400"
$LM stop meeting/t1 >/dev/null 2>&1

echo "[T4] agent mode: poll / reply"
export LABMEET_ANSWER_MODE=agent
$LM open meeting/t1 --no-open >/dev/null 2>&1
PORT=$(python3 -c "import json;print(json.load(open('meeting/t1/.state/server.json'))['port'])")
( tmo 40 $LM poll meeting/t1 >p.out 2>/dev/null; echo "exit=$?" >>p.out ) &
sleep 2
curl -s -X POST "http://127.0.0.1:$PORT/api/threads" -H 'Content-Type: application/json' \
  -d '{"slide":1,"x":10,"y":20,"quote":"3.1 A RMSD","text":"is that median or mean?"}' >/dev/null
wait
chk "poll exit 0"       "$(grep -c 'exit=0' p.out)" "1"
chk "poll got question" "$(python3 -c "import json;print(json.loads(open('p.out').readline())['question'])")" "is that median or mean?"
chk "poll carries anchor+quote" "$(python3 -c "
import json;a=json.loads(open('p.out').readline())['anchor'];print(int(a['x']),a['quote'])")" "10 3.1 A RMSD"
chk "poll carries slide text" "$(python3 -c "
import json;print('RMSD' in json.loads(open('p.out').readline())['slide_text'])")" "True"
T2ID=$(python3 -c "import json;print(json.loads(open('p.out').readline())['thread'])")
$LM reply meeting/t1 --thread $T2ID 'Median, see **eval.py:42**' >/dev/null 2>&1
chk "reply lands in the thread" "$(curl -s "http://127.0.0.1:$PORT/api/threads" | python3 -c "
import json,sys
t=[x for x in json.load(sys.stdin)['threads'] if x['id']=='$T2ID'][0]
print(t['messages'][-1]['role'], 'eval.py' in t['messages'][-1]['text'])")" "agent True"
chk "threads verb lists them" "$($LM threads meeting/t1 2>/dev/null | grep -c 'slide')" "2"
chk "unknown thread reply = 2" "$($LM reply meeting/t1 --thread zz 'x' >/dev/null 2>&1; echo $?)" "2"

echo "[T4b] a missing answerer must not hide the question"
export LABMEET_ANSWER_MODE=codex
export LABMEET_ANSWER_CMD='["/nonexistent/answerer-binary"]'
$LM stop meeting/t1 >/dev/null 2>&1
$LM open meeting/t1 --no-open >/dev/null 2>&1
PORTX=$(python3 -c "import json;print(json.load(open('meeting/t1/.state/server.json'))['port'])")
curl -s -X POST "http://127.0.0.1:$PORTX/api/threads" -H 'Content-Type: application/json' \
  -d '{"slide":1,"x":50,"y":50,"text":"answerer is missing"}' >/dev/null
sleep 2
chk "no fake answer posted" "$(curl -s "http://127.0.0.1:$PORTX/api/threads" | python3 -c "
import json,sys
t=[x for x in json.load(sys.stdin)['threads'] if x['messages'][0]['text']=='answerer is missing'][0]
print(len(t['messages']), t['messages'][-1]['role'])")" "1 user"
chk "still deliverable to poll" "$(tmo 40 $LM poll meeting/t1 2>/dev/null | python3 -c "
import json,sys;print(json.load(sys.stdin)['question'])")" "answerer is missing"
chk "status flags the missing answerer" "$(curl -s "http://127.0.0.1:$PORTX/api/status" | python3 -c "
import json,sys;print(json.load(sys.stdin)['answerer_ready'])")" "False"
$LM reply meeting/t1 "answered by hand" >/dev/null 2>&1   # clear it so later counts are stable
$LM stop meeting/t1 >/dev/null 2>&1
export LABMEET_ANSWER_CMD="[\"$W/stub-answerer.sh\"]"
export LABMEET_ANSWER_MODE=agent
$LM open meeting/t1 --no-open >/dev/null 2>&1
PORT=$(python3 -c "import json;print(json.load(open('meeting/t1/.state/server.json'))['port'])")

echo "[T5] a dead poll client must not swallow a comment"
curl -s -X POST "http://127.0.0.1:$PORT/api/threads" -H 'Content-Type: application/json' \
  -d '{"slide":1,"x":5,"y":5,"text":"broken pipe probe"}' >/dev/null
tmo 12 sh -c "python3 $SKILL/scripts/labmeet.py poll meeting/t1 2>/dev/null | true" >/dev/null 2>&1
chk "still awaiting an answer" "$(curl -s "http://127.0.0.1:$PORT/api/status" | python3 -c "
import json,sys;print(json.load(sys.stdin)['pending'])")" "1"
chk "redelivered to a live poll" "$(tmo 40 $LM poll meeting/t1 2>/dev/null | python3 -c "
import json,sys;print(json.load(sys.stdin)['question'])")" "broken pipe probe"

echo "[T6] end -> exits -> reopen keeps every thread"
$LM end meeting/t1 >/dev/null 2>&1
sleep 1
chk "browser sees ended" "$(curl -s "http://127.0.0.1:$PORT/api/status" | python3 -c "
import json,sys;print(json.load(sys.stdin)['ended'])")" "True"
PID=$(python3 -c "import json;print(json.load(open('meeting/t1/.state/server.json'))['pid'])" 2>/dev/null)
for i in $(seq 1 25); do sleep 1; ps -p "$PID" >/dev/null || break; done
chk "server exited" "$(ps -p "$PID" >/dev/null && echo alive || echo gone)" "gone"
$LM open meeting/t1 --no-open >/dev/null 2>&1
PORT2=$(python3 -c "import json;print(json.load(open('meeting/t1/.state/server.json'))['port'])")
chk "threads survived" "$(curl -s "http://127.0.0.1:$PORT2/api/threads" | python3 -c "
import json,sys;print(len(json.load(sys.stdin)['threads']))")" "4"
chk "answers survived" "$(curl -s "http://127.0.0.1:$PORT2/api/threads" | python3 -c "
import json,sys
print(sum(1 for t in json.load(sys.stdin)['threads'] for m in t['messages'] if m['role']=='agent'))")" "4"
$LM export meeting/t1 >/dev/null 2>&1
chk "markdown feedback export" "$([ -f meeting/t1/slide-feedback.md ] && echo y)" "y"
chk "json feedback export" "$([ -f meeting/t1/slide-feedback.json ] && echo y)" "y"
chk "export keeps slide anchor and chat" "$(python3 -c "import json;d=json.load(open('meeting/t1/slide-feedback.json'));t=d['threads'][0];print(t['slide'],int(t['x']),len(t['messages'])>=2,bool(d['source_presentation']))")" "2 40 True True"
$LM stop meeting/t1 >/dev/null 2>&1
unset LABMEET_ANSWER_MODE

echo "[T7] local-only, stdlib-only, crash-safe"
chk "stdlib-only import" "$(python3 -S -c "import sys;sys.path.insert(0,'$SKILL/scripts');import convert,labmeet,pptx_reader;print('ok')" 2>&1)" "ok"
chk "only 127.0.0.1" "$(grep -ohE 'https?://[^\"% )]*' $SKILL/scripts/*.py $SKILL/assets/* | grep -v '127.0.0.1' | grep -vE 'schemas.openxmlformats.org|example.com' | wc -l | tr -d ' ')" "0"
chk "torn log keeps records" "$(python3 - <<PY
import sys, os
sys.path.insert(0, os.path.expanduser("$SKILL/scripts"))
import labmeet
st = labmeet.Store("meeting/t1")
open(st.log, "a").write('{"op":"msg","thre')      # torn tail, no newline
t, _ = st.new_thread(1, 5, 5, "after the tear")
st2 = labmeet.Store("meeting/t1")
print("ok" if any(x["messages"] and x["messages"][0]["text"] == "after the tear"
                  for x in st2.snapshot()) else "lost")
PY
)" "ok"
chk "interrupted answer not stuck" "$(python3 - <<PY
import sys, os
sys.path.insert(0, os.path.expanduser("$SKILL/scripts"))
import labmeet
st = labmeet.Store("meeting/t1")
t, _ = st.new_thread(1, 9, 9, "q")
st.add_message(t["id"], "agent", "", status="pending")
print(labmeet.Store("meeting/t1").threads[t["id"]]["messages"][-1]["status"])
PY
)" "failed"
sleep 6 & IMP=$!
python3 -c "import json;json.dump({'port':59999,'pid':$IMP,'started_at':'x'},open('meeting/t1/.state/server.json','w'))"
$LM stop meeting/t1 >/dev/null 2>&1
chk "never signals a recycled pid" "$(ps -p $IMP >/dev/null && echo alive || echo killed)" "alive"
kill $IMP 2>/dev/null; wait $IMP 2>/dev/null

echo "[T8] converter safeguards"
printf '# One\n![x](https://evil.example.com/t.png)\n' > remote.md
$CV remote.md --name t8r --out meeting/t8r >/dev/null 2>&1
chk "remote image never auto-loads" "$(grep -cE '<img[^>]*src="https?://' meeting/t8r/index.html)" "0"
mkdir -p da db && printf 'AAA' > da/f.png && printf 'BBBB' > db/f.png
printf '# A\n![x](da/f.png)\n\n## B\n![y](db/f.png)\n' > coll.md
$CV coll.md --name t8c --out meeting/t8c >/dev/null 2>&1
chk "same-name assets kept distinct" "$(ls meeting/t8c/slides/assets | wc -l | tr -d ' ')" "2"
mkdir -p ds/images && printf 'IMG' > ds/images/c.png
printf '<html><body><h1>H</h1><p><img src="images/c.png"></p></body></html>' > ds/p.html
$CV ds/p.html --name t8h --out meeting/t8h >/dev/null 2>&1
chk "relative html asset copied" "$(grep -c 'slides/assets/' meeting/t8h/index.html)" "1"

printf '<html><head><style>@import url("https://f.example.com/x.css");.a{background:url(https://t.example.com/p.png)}</style></head><body><h1>H</h1><p>x</p></body></html>' > styled.html
$CV styled.html --name t8s --out meeting/t8s >/dev/null 2>&1
chk "preserved css cannot phone home" "$(python3 -c "
import re
h=open('meeting/t8s/index.html').read()
s=re.search(r'<style.*?</style>', h, re.S)
print(len(re.findall(r'https?://|@import\s+url', s.group(0) if s else '')))")" "0"
chk "preserved css is still kept" "$(grep -c '<style>' meeting/t8s/index.html)" "1"

echo "[T8b] default artifacts stay out of the working tree"
mkdir -p project cache
printf '# Cached\nroom\n' > project/cached.md
(cd project && LABMEET_CACHE_DIR="$W/cache" $CV cached.md >/dev/null 2>&1)
CACHED="$W/cache/cached-$(python3 -c "import hashlib,os;print(hashlib.sha256(os.path.realpath('$W/project/cached.md').encode()).hexdigest()[:12])")"
chk "default room is in cache" "$([ -f "$CACHED/index.html" ] && echo y)" "y"
chk "project tree has no meeting artifacts" "$([ ! -e project/meeting ] && echo y)" "y"
chk "cache root is private" "$(python3 -c "import os,stat;print(oct(stat.S_IMODE(os.stat('$W/cache').st_mode)))")" "0o700"

echo "[extra] error contracts"
chk "unknown flag = 2"    "$($CV deck.md --nope >/dev/null 2>&1; echo $?)" "2"
chk "unknown mode = 2"    "$($CV deck.md --mode sideways >/dev/null 2>&1; echo $?)" "2"
chk "unknown verb = 2"    "$($LM bogus x >/dev/null 2>&1; echo $?)" "2"
chk "bad input = 1"       "$($CV missing.md >/dev/null 2>&1; echo $?)" "1"
chk "legacy .ppt refused" "$(touch old.ppt; $CV old.ppt 2>&1 | grep -c 'save as .pptx')" "1"
chk "no-args lists state" "$($LM | grep -c 'help:')" "1"

echo "[T9] persisted Codex fork answerer"
if python3 "$SKILL/tests/test_fork_answerer.py"; then
  ok "one fork answers questions and follow-ups"
else
  no "persisted fork answerer"
fi

echo
echo "PASS=$PASS FAIL=$FAIL SKIP=$SKIP   workdir=$W"
[ "$FAIL" -eq 0 ]
