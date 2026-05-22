interface LibRowProps {
  icon: React.ReactNode;
  name: string;
  meta: string;
}

export function LibRow({ icon, name, meta }: LibRowProps) {
  return (
    <div
      draggable
      className="flex cursor-grab items-center gap-[9px] rounded-[7px] border border-transparent px-2 py-[7px] hover:border-line-soft hover:bg-bg-hover"
    >
      {/* .icon */}
      <span className="inline-flex h-[22px] w-[22px] items-center justify-center rounded-[5px] bg-bg-sunken text-ink-2">
        {icon}
      </span>
      <span className="flex-1 text-12 text-ink">{name}</span>
      <span className="font-mono text-10.5 text-ink-5">{meta}</span>
    </div>
  );
}
