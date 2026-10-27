import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { AlertTriangle, FlaskConical, Play } from "lucide-react";
import { cn } from "../../lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-md text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-45",
  {
    variants: {
      variant: {
        default:
          "bg-blue-600 text-white hover:bg-blue-500 shadow-sm border border-blue-500/40",
        backtest:
          "bg-blue-600 text-white hover:bg-blue-500 shadow-sm border border-blue-400/30 font-semibold",
        shadow:
          "bg-amber-500/12 text-amber-200 border border-amber-500/55 hover:bg-amber-500/22 font-semibold",
        liveDanger:
          "bg-rose-600 text-white hover:bg-rose-500 border border-rose-400/60 shadow-[0_0_14px_rgba(244,63,94,0.28)] font-semibold",
        killSwitch:
          "bg-rose-700 text-white hover:bg-rose-600 border-2 border-rose-400/80 shadow-[0_0_16px_rgba(244,63,94,0.35)] font-bold tracking-wider uppercase",
        secondary:
          "bg-secondary text-secondary-foreground hover:bg-slate-700/80 border border-border",
        outline:
          "border border-border bg-transparent text-slate-200 hover:bg-slate-800/70",
        ghost:
          "bg-transparent text-slate-300 hover:bg-slate-800/70 hover:text-white",
      },
      size: {
        sm: "h-7 px-2.5 text-[11px]",
        default: "h-8 px-3.5 text-xs",
        lg: "h-9 px-4 text-sm",
        icon: "h-8 w-8 p-0",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, ...props }, ref) => {
    return (
      <button
        ref={ref}
        className={cn(buttonVariants({ variant, size, className }))}
        {...props}
      />
    );
  }
);
Button.displayName = "Button";

/**
 * Physical isolation wrapper required around any capital-moving (LIVE) action button.
 * Prevents accidental clicks next to safe Backtest/Shadow controls.
 */
export function IsolatedDangerZone({
  title = "实盘资金操作隔离区 (LIVE CAPITAL ZONE)",
  subtitle,
  children,
  className,
}: {
  title?: string;
  subtitle?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "danger-isolation-stripe rounded-lg border border-rose-500/45 p-3 mt-3",
        className
      )}
    >
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wider text-rose-300">
            <AlertTriangle className="h-3.5 w-3.5 text-rose-400 shrink-0" />
            <span>{title}</span>
          </div>
          {subtitle && (
            <p className="text-[11px] text-slate-400 mt-0.5">{subtitle}</p>
          )}
        </div>
        <div className="shrink-0">{children}</div>
      </div>
    </div>
  );
}

/**
 * Pre-configured tri-environment launch buttons enforcing Principle 1 visual & spatial separation.
 */
export function BacktestActionButton(props: ButtonProps) {
  return (
    <Button variant="backtest" {...props}>
      <Play className="h-3.5 w-3.5" />
      <span>{props.children ?? "发起回测"}</span>
    </Button>
  );
}

export function ShadowActionButton(props: ButtonProps) {
  return (
    <Button variant="shadow" {...props}>
      <FlaskConical className="h-3.5 w-3.5 text-amber-400" />
      <span>{props.children ?? "发起影子交易"}</span>
      <span className="ml-1 rounded bg-amber-500/25 px-1.5 py-0.5 text-[10px] font-mono uppercase tracking-wider text-amber-200">
        SHADOW
      </span>
    </Button>
  );
}

export function LiveDangerActionButton(props: ButtonProps) {
  return (
    <Button variant="liveDanger" {...props}>
      <AlertTriangle className="h-3.5 w-3.5" />
      <span>{props.children ?? "部署至实盘 (LIVE)"}</span>
    </Button>
  );
}

export { Button, buttonVariants };
