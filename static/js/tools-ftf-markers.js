// FTF markers tool (templates/geocaches/tools/tools_ftf_markers.html): verify
// one row's "first found log" suggestion, or verify all of them in sequence
// with a progress indicator. Each row's verify button carries its own POST
// URL and pk via data-* attributes, so no page-level config element is needed.

function _ftfGetCsrf() {
  const el = document.querySelector('[name=csrfmiddlewaretoken]');
  return el ? el.value : '';
}

async function _ftfVerifyOne(btn) {
  const pk = btn.dataset.pk;
  const url = btn.dataset.url;
  const target = document.getElementById('ftf-row-' + pk);
  const spinner = document.getElementById('verify-spinner-' + pk);
  if (spinner) spinner.classList.remove('d-none');
  btn.disabled = true;
  try {
    const resp = await fetch(url, {
      method: 'POST',
      headers: {'X-CSRFToken': _ftfGetCsrf()},
      credentials: 'same-origin',
    });
    if (resp.ok) {
      target.outerHTML = await resp.text();
    }
  } catch (e) {
    console.error('FTF verify failed for pk=' + pk, e);
    btn.disabled = false;
    if (spinner) spinner.classList.add('d-none');
  }
}

function gcfVerifySingleFTF(btn) {
  _ftfVerifyOne(btn);
}

async function gcfVerifyAllFTF() {
  const btns = Array.from(document.querySelectorAll('.ftf-verify-btn'));
  if (!btns.length) return;

  const masterBtn = document.getElementById('btn-verify-all');
  masterBtn.disabled = true;

  const progress = document.getElementById('verify-progress');
  const doneEl = document.getElementById('verify-done');
  const totalEl = document.getElementById('verify-total');
  const completeEl = document.getElementById('verify-complete');

  totalEl.textContent = btns.length;
  doneEl.textContent = '0';
  progress.classList.remove('d-none');
  completeEl.classList.add('d-none');

  let done = 0;
  for (const b of btns) {
    await _ftfVerifyOne(b);
    done++;
    doneEl.textContent = done;
  }

  progress.classList.add('d-none');
  completeEl.classList.remove('d-none');
  masterBtn.textContent = gettext("Done");
}
