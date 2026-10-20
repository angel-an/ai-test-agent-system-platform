// TODO  MC8yOmFIVnBZMlhscm9ua3VMazZWR1l5VWc9PTpmN2YyNGJiMQ==

import { cn } from "@/lib/utils";

function Skeleton({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("animate-pulse rounded-md bg-muted", className)}
      {...props}
    />
  );
}
// NOTE  MS8yOmFIVnBZMlhscm9ua3VMazZWR1l5VWc9PTpmN2YyNGJiMQ==

export { Skeleton };
