'use strict';
(() => {
  const container = document.querySelector('#briefing-content');
  const button = document.querySelector('#briefing-refresh');
  let loading = false;
  function item(parent, text) { parent.append(node('p', 'briefing-item', text)); }
  function card(title, data, draw) {
    const section = node('article', 'briefing-section');
    section.append(node('h3', '', title));
    if (data.status === 'ok') draw(section);
    else {
      item(section, data.message);
      if (data.status === 'needs_attention') {
        const link = node('a', 'setup-action', 'Connection setup ↗');
        link.href = '/connections/setup'; section.append(link);
      }
    }
    container.append(section);
  }
  async function load() {
    if (loading) return;
    loading = true; button.disabled = true;
    document.querySelector('#briefing-error').textContent = '';
    try {
      const data = await api('/briefing');
      container.replaceChildren();
      card('Tasks', data.tasks, el => {
        item(el, `${data.tasks.overdue_count} overdue · ${data.tasks.due_today_count} due today`);
        for (const task of data.tasks.overdue) item(el, `${task.title} · due ${task.due_date}`);
        for (const task of data.tasks.due_today) item(el, `${task.title} · today`);
        item(el, `${data.tasks.open_count} open · ${data.tasks.high_priority_count} high priority`);
      });
      card('Remaining today', data.calendar, el => {
        if (!data.calendar.events.length) item(el, 'No remaining events today.');
        for (const event of data.calendar.events) {
          const start = event.start;
          let label = start.date ? 'All day' : 'Time unspecified';
          if (start.dateTime) {
            const date = new Date(start.dateTime);
            if (!Number.isNaN(date.valueOf())) label = new Intl.DateTimeFormat('en', {timeZone: data.timezone, hour: 'numeric', minute: '2-digit'}).format(date);
          }
          item(el, `${label} · ${event.title}`);
        }
        if (data.calendar.has_more) item(el, 'More events in Calendar.');
      });
      card('Gmail', data.gmail, el => item(el, `${data.gmail.unread_count} unread inbox messages`));
      card('Recent public GitHub activity', data.github, el => {
        if (!data.github.events.length) item(el, 'No recent public activity.');
        for (const event of data.github.events) item(el, `${event.type || 'Activity'} · ${event.repository || 'Repository'}${event.created_at ? ' · ' + event.created_at.slice(0,10) : ''}`);
      });
      document.querySelector('#briefing-updated').textContent = `Updated ${new Intl.DateTimeFormat('en', {timeZone:data.timezone, hour:'numeric', minute:'2-digit'}).format(new Date(data.generated_at))} · ${data.timezone}${data.timezone_fallback ? ' (default timezone)' : ''}`;
    } catch (error) {
      container.replaceChildren();
      document.querySelector('#briefing-updated').textContent = '';
      document.querySelector('#briefing-error').textContent = error.message;
    } finally { loading = false; button.disabled = false; }
  }
  button.addEventListener('click', load);
  document.querySelector('#refresh').addEventListener('click', load);
  load();
  setInterval(() => { if (!document.hidden && (!location.hash || location.hash === '#overview')) load(); }, 300000);
})();
