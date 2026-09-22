// Trackable split view (templates/geocaches/trackable_split.html): clicking
// a row in the left table loads that trackable's detail into the right pane
// via htmx (embedded, no full navigation). Re-binds after every htmx swap of
// #tb-table-container since new rows carry no listeners yet.

function _gcfTbSplitBindRows() {
  document.querySelectorAll('#tb-table-container tr').forEach(function(tr) {
    var link = tr.querySelector('a.cache-name-link');
    if (!link || tr.dataset.tbBound === '1') return;
    tr.dataset.tbBound = '1';
    tr.style.cursor = 'pointer';
    tr.addEventListener('click', function(e) {
      if (e.target.closest('a')) return;
      htmx.ajax('GET', link.href + '?embed=1', {
        target: '#tb-detail-pane',
        select: '#tb-detail-content',
        swap:   'innerHTML',
      });
    });
  });
}
document.addEventListener('DOMContentLoaded', _gcfTbSplitBindRows);
document.body.addEventListener('htmx:afterSwap', function(e) {
  if (e.target && e.target.id === 'tb-table-container') _gcfTbSplitBindRows();
});
