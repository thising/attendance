document.querySelector('#report-period')?.addEventListener('change', event => {
  document.querySelectorAll('[data-report-table]').forEach(section => {
    section.hidden = section.dataset.reportTable !== event.target.value;
  });
});

document.querySelector('#report-sort')?.addEventListener('change', event => {
  const mode = event.target.value;
  const sign = mode.endsWith('desc') ? -1 : 1;
  const number = new Intl.Collator('zh-CN', {numeric: true, sensitivity: 'variant'});
  const pinyin = new Intl.Collator('zh-CN-u-co-pinyin', {sensitivity: 'variant'});
  document.querySelectorAll('[data-report-table] tbody').forEach(body => {
    const rows = Array.from(body.querySelectorAll('tr[data-number]'));
    rows.sort((a, b) => sign * (mode.startsWith('pinyin')
      ? pinyin.compare(a.dataset.name, b.dataset.name) || number.compare(a.dataset.number, b.dataset.number)
      : number.compare(a.dataset.number, b.dataset.number)));
    body.append(...rows);
  });
});
