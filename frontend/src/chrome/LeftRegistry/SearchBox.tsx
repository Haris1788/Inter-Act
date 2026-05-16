export function SearchBox() {
  return (
    <div className="border-b border-line-soft px-3 pb-2 pt-3">
      {/* .search */}
      <div className="flex h-[30px] items-center gap-[6px] rounded-[7px] border border-line bg-bg-surface px-[10px] text-ink-4">
        {/* search icon */}
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
          <circle cx="11" cy="11" r="7" />
          <line x1="21" y1="21" x2="16.6" y2="16.6" />
        </svg>
        <input
          className="flex-1 border-0 bg-transparent text-12.5 text-ink outline-none placeholder:text-ink-4"
          placeholder="Search actors, capabilities…"
        />
        <span className="rounded-[4px] border border-line bg-bg-surface px-1 font-mono text-10.5 text-ink-4">
          /
        </span>
      </div>
    </div>
  );
}
