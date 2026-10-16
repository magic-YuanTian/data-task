// About the benchmark: the seven levels and 21 task types. Clicking a level or a task type explains it in place.
import { html, useState, useApp } from '../lib.js';
import { LevelTag } from '../ui.js';

const LEVEL_TEXT = {
  Q1: 'What a field, value or document means.',
  Q2: 'Look up or compute an exact value.',
  Q3: 'Summarize trends, cycles, distributions and relationships.',
  Q4: 'Find anomalies, shifts and associations without being told where.',
  Q5: 'Explain why something changed, and by how much.',
  Q6: 'Forecast values and the consequences of a change.',
  Q7: 'Recommend or optimize an action under constraints.',
};


const LEVEL_EXPLAIN = {
  Q1: 'Understand the data before using it: what a column, a code or a term means, and what the documents say.',
  Q2: 'Get an exact answer straight from the data: look up records, filter them, count, sum and rank.',
  Q3: 'Summarize how the data behave: trends over time, repeating cycles, how values are spread, and how two measures move together.',
  Q4: 'Notice what nobody pointed to: unusual days, sudden changes in a pattern, or links between measures, without being told where to look.',
  Q5: 'Explain why something happened, and how much each cause contributed.',
  Q6: 'Say what will happen next, or what would happen if something changed.',
  Q7: 'Recommend what to do: give advice backed by data, or find the best plan within given limits.',
};
const TYPE_EXPLAIN = {
  'Q1.1': 'What does a column, a value or a term mean? For example, what a status code of 3 stands for in an orders table.',
  'Q1.2': 'Read a document and explain what it says, for example how an experiment was designed or which units a data feed uses.',
  'Q2.1': 'Find the records that meet some conditions, for example all orders over $500 placed last week.',
  'Q2.2': 'Count, sum or average by group and rank the groups, for example the ten best-selling products last month.',
  'Q2.3': 'Find one specific record from a loose description, for example the order a customer describes from memory.',
  'Q2.4': 'Look up a specific fact in a document, such as a price or a rule.',
  'Q3.1': 'Describe how a measure changes over a long period, beyond the seasonal ups and downs.',
  'Q3.2': 'Describe patterns that repeat, by hour, by weekday or by season.',
  'Q3.3': 'Describe how values are spread: the typical value, the range, the skew and the outliers.',
  'Q3.4': 'Describe how two measures move together, for example sales and temperature.',
  'Q4.1': 'Find days or periods that are unusually high or low, and say what happened.',
  'Q4.2': 'Notice when the shape of a distribution changes, and tell a real change from a change in how the data are recorded.',
  'Q4.3': 'Find which factors are linked to an outcome, and how strongly.',
  'Q4.open': 'An open request: look at the data and report anything worth knowing.',
  'Q5.1': 'Explain why a measure changed and rank the causes. Some questions suggest a wrong cause on purpose.',
  'Q5.2': 'Say how much each part or cause contributed to a change.',
  'Q6.1': 'Forecast a number for a later period from earlier data.',
  'Q6.2': 'Predict how a measure reacts to a known event, such as a heat wave or a closure.',
  'Q6.3': 'Estimate what would happen under a change that was not made, such as a new price.',
  'Q7.1': 'Recommend actions and support them with data. Answers are graded with a checklist of points.',
  'Q7.2': 'Find the best plan under given limits, for example a delivery plan at the lowest cost. The best answer can be computed exactly.',
};

export function AboutPage() {
  const app = useApp();
  const { meta } = app;
  const [sel, setSel] = useState(null);          // a level (Q3) or a task type (Q3.2)
  const toggle = (k) => setSel(sel === k ? null : k);
  const byLevel = Object.fromEntries(meta.level_order.map((lv) => [lv, Object.keys(meta.task_types).filter((t) => t.startsWith(lv + '.'))]));
  const selLevel = sel && sel.split('.')[0];
  return html`<div class="page about">
    <div class="hero about-hero">
      <div>
        <h1 style="font-size:28px">DataTaskBench</h1>
        <p class="lead">A benchmark of data analysis tasks on real data. The tasks span seven levels, from what a field
          means to what decision to make, and 21 task types within them. Every task comes with its ground-truth
          reasoning path: the steps of the analysis, the query or source behind each step, and the result it gives.</p>
      </div>
      <div class="ladder" style="height:170px">
        ${meta.level_order.map((lv, i) => html`<button class=${'rung-btn' + (selLevel === lv ? ' on' : '')} onClick=${() => toggle(lv)}>
          <div class=${'rung ' + lv} style=${`height:${28 + i * 20}px`}>${lv}</div>
          <div class="lbl">${meta.levels[lv]}</div></button>`)}
      </div>
    </div>

    <div class="section-label">Task types</div>
    <div class="levels" style="margin-bottom:28px">
      ${meta.level_order.map((lv) => html`<div class=${'lvl' + (selLevel === lv ? ' on' : '')}>
        <button class="lvl-head" onClick=${() => toggle(lv)}>
          <${LevelTag} level=${lv} /><div>${meta.levels[lv]}</div><p>${LEVEL_TEXT[lv]}</p>
          ${sel === lv && html`<p class="explain">${LEVEL_EXPLAIN[lv]}</p>`}</button>
        ${byLevel[lv].map((tt) => html`<button class=${'tt' + (sel === tt ? ' on' : '')} onClick=${() => toggle(tt)}>
          <span class="code">${tt.split('.')[1]}</span><span>${meta.task_types[tt]}
          ${sel === tt && html`<span class="explain">${TYPE_EXPLAIN[tt]}</span>`}</span></button>`)}
      </div>`)}
    </div>
  </div>`;
}
