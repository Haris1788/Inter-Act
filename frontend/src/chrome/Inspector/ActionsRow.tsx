export function ActionsRow() {
  return (
    <div className="grid grid-cols-2 gap-2 px-[14px] pt-[6px] pb-[14px]">
      <button className="h-[30px] px-[10px] rounded-[7px] inline-flex items-center justify-center gap-[6px] bg-bg-surface border border-line text-12 text-ink-2 hover:bg-bg-hover">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <path d="M3 7v6h6"/>
          <path d="M21 17a9 9 0 0 0-15-6.7L3 13"/>
        </svg>
        Re-bid
      </button>
      <button className="h-[30px] px-[10px] rounded-[7px] inline-flex items-center justify-center gap-[6px] bg-white border border-[#E6C6BA] text-12 text-[#8B3621] hover:bg-[#F6E8DF]">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
          <circle cx="12" cy="12" r="9"/>
          <line x1="8" y1="8" x2="16" y2="16"/>
          <line x1="16" y1="8" x2="8" y2="16"/>
        </svg>
        Stop actor
      </button>
    </div>
  );
}
