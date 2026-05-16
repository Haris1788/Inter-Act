import { cn } from "@/lib/cn";

interface FieldProps {
  label: string;
  children: React.ReactNode;
  className?: string;
}

export function Field({ label, children, className }: FieldProps) {
  return (
    <div className={cn("grid gap-[10px] py-1", "grid-cols-[108px_1fr]", "items-center", className)}>
      <span className="text-12 text-ink-3">{label}</span>
      <div className="min-w-0">{children}</div>
    </div>
  );
}
