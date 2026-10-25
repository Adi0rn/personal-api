'use strict';
(() => {
  const dialog = $('#reminder-dialog');
  let loading = false;
  function open(task = null) {
    const select = $('#reminder-task');
    select.replaceChildren(new Option('General reminder', ''));
    for (const entry of state.tasks.filter(x => !x.completed)) select.add(new Option(entry.title, entry.id));
    select.value = task ? String(task.id) : '';
    $('#reminder-title').value = task?.title || '';
    const date = new Date(Date.now() + 3600000); date.setSeconds(0, 0);
    const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
    $('#reminder-time').value = local.toISOString().slice(0, 16);
    $('#reminder-form-error').textContent = '';
    dialog.showModal();
  }
  window.papiReminders = {open};
  async function load() {
    if (loading) return;
    loading = true;
    try {
      const [data, options] = await Promise.all([api('/reminders'), api('/reminders/settings')]);
      $('#meeting-reminders').checked = options.calendar_enabled;
      $('#meeting-lead').value = String(options.lead_minutes);
      $('#reminder-health').textContent = options.calendar_enabled ? options.calendar_error || 'Meeting reminders enabled · primary Google calendar' : 'Meeting reminders off';
      const list = $('#reminder-list'); list.replaceChildren();
      if (!data.reminders.length) list.append(node('p', 'empty', 'No reminders scheduled.'));
      for (const entry of data.reminders) {
        const row = node('div', 'reminder-row');
        const body = node('div');
        body.append(node('strong', '', entry.title), node('small', '', `${new Date(entry.due_at).toLocaleString()} · ${entry.kind} · ${entry.state === 'submitted' ? 'Submitted to Windows' : entry.state}`));
        if (entry.message) body.append(node('small', 'inline-error', entry.message));
        row.append(body);
        if (['pending', 'failed', 'missed', 'submitted'].includes(entry.state)) {
          const cancel = node('button', 'text-button', entry.state === 'pending' ? 'Cancel' : 'Dismiss');
          cancel.type = 'button'; cancel.setAttribute('aria-label', 'Dismiss reminder ' + entry.title);
          cancel.addEventListener('click', () => busy(cancel, async () => { await api('/reminders/' + entry.id, {method:'DELETE'}); await load(); }));
          row.append(cancel);
        }
        list.append(row);
      }
      $('#reminder-error').textContent = '';
    } catch (error) { $('#reminder-error').textContent = error.message; }
    finally { loading = false; }
  }
  $('#add-reminder').addEventListener('click', () => open());
  $('#reminder-task').addEventListener('change', () => {
    const task = state.tasks.find(x => String(x.id) === $('#reminder-task').value);
    if (task) $('#reminder-title').value = task.title;
  });
  $('#reminder-form').addEventListener('submit', async event => {
    event.preventDefault(); const button = $('#save-reminder'); button.disabled = true;
    try {
      const date = new Date($('#reminder-time').value);
      if (!Number.isFinite(date.valueOf()) || date <= new Date()) throw new Error('Choose a reminder time in the future.');
      await post('/reminders', {title:$('#reminder-title').value.trim(), due_at:date.toISOString(), task_id:$('#reminder-task').value ? Number($('#reminder-task').value) : null});
      dialog.close(); await load(); toast('Reminder scheduled.');
    } catch (error) { $('#reminder-form-error').textContent = error.message; }
    finally { button.disabled = false; }
  });
  $('#reminder-settings-save').addEventListener('click', () => busy($('#reminder-settings-save'), async () => {
    await api('/reminders/settings', {method:'PUT',body:JSON.stringify({calendar_enabled:$('#meeting-reminders').checked,lead_minutes:Number($('#meeting-lead').value)})});
    await load(); toast('Reminder settings saved.');
  }));
  $('#test-notification').addEventListener('click', () => busy($('#test-notification'), async () => {
    const result = await post('/reminders/test', {}); toast(result.message);
  }));
  $('#refresh').addEventListener('click', load);
  load(); setInterval(() => { if (!document.hidden && !dialog.open) load(); }, 30000);
})();
