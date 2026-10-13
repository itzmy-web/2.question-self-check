/**
 * 前端纯逻辑测试（不依赖浏览器 DOM）。
 *
 * 直接从 quiz.html 里「切片」出真实的业务函数来跑，而不是重写一遍，
 * 所以测的就是线上那份代码。
 * （v3 企业版改版后，旧 26 功能代码迁移至 quiz.html，本测试随之指向 quiz.html）
 *
 * 覆盖：
 *   A. 方面(topic) 推导 deriveTopic
 *   B. 方面统计 buildAspects
 *   C. 总结渲染 summaryHTML（薄弱/掌握分组）
 *   D. 【回归】复习模式必须先快照题目集合，否则做对一题就跳过下一题
 *   E. quizLeft / quizUnfinished（退出拦截的判断依据）
 */
const fs = require('fs');
const path = require('path');

const HTML = path.join(__dirname, '..', 'quiz.html');
const src = fs.readFileSync(HTML, 'utf8');
const js = src.split('<script>').slice(1).join('<script>').split('</script>')[0];

const PASS = [], FAIL = [];
function check(name, cond, extra) {
  (cond ? PASS : FAIL).push(name);
  console.log((cond ? '  ✅ ' : '  ❌ ') + name + (cond ? '' : '  <-- ' + JSON.stringify(extra)));
}

function slice(from, to) {
  const a = js.indexOf(from);
  const b = js.indexOf(to, a);
  if (a < 0 || b < 0) throw new Error(`切片失败: ${from} -> ${to}`);
  return js.slice(a, b);
}

// ---------- 公共桩 ----------
const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

// ================= A. deriveTopic / buildAspects / summaryHTML =================
const topicCode = slice('const STOP_HEAD=', '/* ---------------- 自检 ---------------- */');
let results = {};
eval(topicCode);

console.log('[A] 方面(topic) 推导');
check('LLM 给了 topic 就直接用', deriveTopic({ topic: '光合作用', question: '随便' }) === '光合作用');
check('  （并会去掉首尾空格）', deriveTopic({ topic: '  合同法  ', question: 'x' }) === '合同法');
check('无 topic 时从题干推导', deriveTopic({ question: '光合作用的场所是？' }) === '光合作用的场所', deriveTopic({ question: '光合作用的场所是？' }));
check('剥掉「下列/关于」等引导词',
  deriveTopic({ question: '下列关于细胞膜的说法正确的是？' }).indexOf('下列') === -1,
  deriveTopic({ question: '下列关于细胞膜的说法正确的是？' }));
check('剥掉题号与「什么/是」',
  deriveTopic({ question: '1. 什么是光合作用？' }) === '光合作用',
  deriveTopic({ question: '1. 什么是光合作用？' }));
check('空题干兜底为「综合」', deriveTopic({}) === '综合', deriveTopic({}));
check('摘要有长度上限（<=8 字）',
  deriveTopic({ question: '量子纠缠态的非定域性在实验上如何被验证' }).length <= 8,
  deriveTopic({ question: '量子纠缠态的非定域性在实验上如何被验证' }));

console.log('\n[B] 方面统计 buildAspects');
const setB = [
  { id: 'a', question: '光合作用是什么', topic: '光合作用' },
  { id: 'b', question: '光合作用的产物', topic: '光合作用' },
  { id: 'c', question: '细胞膜结构', topic: '细胞结构' },
  { id: 'd', question: '细胞膜功能', topic: '细胞结构' },
];
results = { a: 'fam', b: 'unfam', c: 'fam', d: 'fam' };
const aspB = buildAspects(setB).sort((x, y) => x.topic.localeCompare(y.topic));
check('聚合出 2 个方面', aspB.length === 2, aspB);
const gz = aspB.find(x => x.topic === '光合作用');
const xb = aspB.find(x => x.topic === '细胞结构');
check('光合作用 1/2 -> 50%', gz && gz.ok === 1 && gz.total === 2 && gz.rate === 50, gz);
check('细胞结构 2/2 -> 100%', xb && xb.ok === 2 && xb.total === 2 && xb.rate === 100, xb);
check('未作答计入 unans 而非 bad',
  buildAspects([{ id: 'z', question: '未答', topic: 'T' }]).find(x => x.topic === 'T').unans === 1);

console.log('\n[C] 总结渲染 summaryHTML');
results = { a: 'fam', b: 'unfam' };
const aspC = buildAspects([
  { id: 'a', question: '光合作用1', topic: '光合作用' },
  { id: 'b', question: '细胞膜1', topic: '细胞结构' },
]);
const htmlC = summaryHTML(aspC, [{ question: '细胞膜1', topic: '细胞结构', result: 'bad' },
{ question: '好题', topic: '光合作用', result: 'ok' }]);
check('含「需要加强的方面」', htmlC.includes('需要加强的方面'));
check('含「掌握较好的方面」', htmlC.includes('掌握较好的方面'));
check('薄弱的进「需要加强」', htmlC.includes('细胞结构'));
check('「没拿下的题」只列 result=bad', htmlC.includes('这次没拿下的题（1）'), htmlC.match(/没拿下的题（\d+）/));
check('展示题数正确（1 道）', !htmlC.includes('没拿下的题（2）'));
check('HTML 被转义（防注入）',
  summaryHTML([{ topic: '<img onerror=x>', total: 1, ok: 0, bad: 1, rate: 0 }], [])
    .includes('&lt;img'), '未转义');
check('全对时薄弱区显示鼓励语',
  summaryHTML([{ topic: 'T', total: 2, ok: 2, bad: 0, rate: 100 }], []).includes('没有明显薄弱环节'));

// ================= D. 复习模式快照（回归） =================
console.log('\n[D] 【回归】复习模式必须先快照题目集合');
const quizCode = slice('function getQuizSet()', 'function renderQuestion(');
let quizList = [], quizSet = [], quizIdx = 0, reviewMode = false, quizActive = false;
const toast = () => { }, showPanel = () => { }, renderQuestion = () => { };
eval(quizCode);

function runRound() {
  // 模拟 startQuiz + 逐题 mark()：每题都答对
  startQuiz(true);
  const seen = [];
  let guard = 0;
  while (quizIdx < getQuizSet().length && guard++ < 50) {
    const q = getQuizSet()[quizIdx];
    seen.push(q.id);
    results[q.id] = 'fam';
    q._done = true;
    quizIdx++;
  }
  return seen;
}

const mk = ids => ids.map(id => ({ id, question: '题' + id }));
quizList = mk(['q0', 'q1', 'q2']);
results = { q0: 'unfam', q1: 'unfam', q2: 'unfam' };
const seen = runRound();
check('复习模式下 3 道题全部做到（不跳题）',
  seen.length === 3 && seen.join(',') === 'q0,q1,q2', seen);

// 反证：如果按「结果反查集合」的老写法，做对一题集合就缩水 -> 必然跳题
results = { q0: 'unfam', q1: 'unfam', q2: 'unfam' };
const oldSeen = [];
let oldIdx = 0, g2 = 0;
while (g2++ < 50) {
  const s = quizList.filter(q => results[q.id] === 'unfam');   // 老实现
  if (oldIdx >= s.length) break;
  const q = s[oldIdx];
  oldSeen.push(q.id);
  results[q.id] = 'fam';
  oldIdx++;
}
check('反证：老写法确实会跳题（说明这个修复是必要的）', oldSeen.length < 3, oldSeen);

console.log('\n[E] quizLeft / quizUnfinished（退出拦截依据）');
quizList = mk(['a', 'b', 'c', 'd']);
results = {};
startQuiz(false);
check('刚开始：4 题未做', quizLeft() === 4, quizLeft());
check('刚开始：算「没做完」', quizUnfinished() === true);
results.a = 'fam'; getQuizSet()[0]._done = true; quizIdx = 1;
check('做 1 题后剩 3', quizLeft() === 3, quizLeft());
check('做 1 题后仍算「没做完」（退出要拦）', quizUnfinished() === true);
getQuizSet().forEach(q => { q._done = true; results[q.id] = 'fam'; });
check('全做完：剩 0', quizLeft() === 0, quizLeft());
check('全做完：不再拦截退出', quizUnfinished() === false);
quizActive = false;
check('已结束后不再拦截', quizUnfinished() === false);

console.log('\n' + '='.repeat(52));
console.log(`通过 ${PASS.length} / ${PASS.length + FAIL.length}`);
if (FAIL.length) {
  console.log('失败项：');
  FAIL.forEach(f => console.log('  - ' + f));
  process.exit(1);
}
console.log('全部通过 ✅');
