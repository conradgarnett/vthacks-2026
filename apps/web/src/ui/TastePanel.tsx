import { useEffect, useState } from 'react';
import { SimBadge, TierBadge } from './Badges';

interface MenuInfo {
  fqdn: string;
  label: string;
  tier: 'VERIFIED' | 'INFERRED' | 'UNVERIFIED';
  items: { id: string; name: string }[];
}

interface Props {
  post: (path: string, body?: unknown) => Promise<unknown>;
  get: (path: string) => Promise<unknown>;
  /** Changes whenever the world changes (used to refresh the menu list). */
  refreshKey: number;
  declared: string[];
}

/** TasteLens: describe food and check declared allergens against verified menus, label text and photos. */
export function TastePanel({ post, get, refreshKey, declared }: Props) {
  const [menus, setMenus] = useState<MenuInfo[]>([]);
  const [item, setItem] = useState('');
  const [label, setLabel] = useState('');
  const [photo, setPhoto] = useState(true);

  useEffect(() => {
    let live = true;
    void (get('/api/menu') as Promise<{ menus: MenuInfo[] }>).then((r) => {
      if (!live) return;
      setMenus(r.menus);
      setItem((cur) => cur || r.menus[0]?.items[0]?.id || '');
    });
    return () => {
      live = false;
    };
  }, [get, refreshKey]);

  const menu = menus.find((m) => m.items.some((i) => i.id === item));

  return (
    <section aria-labelledby="taste-h" className="panel">
      <h2 id="taste-h">
        TasteLens: food and allergens <SimBadge label="SAMPLE MENU" />
      </h2>
      <p className="meta">
        Declared allergens ({declared.length ? declared.join(', ') : 'none yet'}) stay on this device. A verified restaurant list outranks
        label text, which outranks a photo. Any source mentioning one of your allergens raises an alert, and “may contain” counts as
        present.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void post('/api/taste', {
            ...(menu && item ? { fqdn: menu.fqdn, itemId: item } : {}),
            ...(label.trim() ? { labelText: label } : {}),
            ...(photo ? { fixture: 'menu-photo' } : {}),
          });
        }}
      >
        <p>
          <label htmlFor="menu-item">Restaurant menu item</label>{' '}
          <select id="menu-item" value={item} onChange={(e) => setItem(e.target.value)} disabled={menus.length === 0}>
            {menus.length === 0 && <option value="">No menu yet: arrive at the restaurant first</option>}
            <option value="">(none)</option>
            {menus.flatMap((m) => m.items.map((i) => <option key={`${m.fqdn}-${i.id}`} value={i.id}>{`${i.name} (${m.label})`}</option>))}
          </select>{' '}
          {menu && <TierBadge tier={menu.tier} />}
        </p>
        <p>
          <label htmlFor="label-text">Label or ingredient text (optional)</label>
          <br />
          <textarea
            id="label-text"
            rows={3}
            cols={44}
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="Ingredients: ... Contains: ... May contain: ..."
          />
        </p>
        <p>
          <label>
            <input type="checkbox" checked={photo} onChange={(e) => setPhoto(e.target.checked)} /> Include the sample dish photo (mock AI,
            works offline)
          </label>
        </p>
        <div className="toolbar">
          <button type="button" onClick={() => void post('/api/arrive', { area: 'bella-cucina' })}>
            Arrive at Bella Cucina
          </button>
          <button type="submit" className="primary">
            Describe and check allergens
          </button>
        </div>
      </form>
    </section>
  );
}
