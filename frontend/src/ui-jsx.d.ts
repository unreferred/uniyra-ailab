// Type shim for untyped shadcn .jsx components.
// @types/react 19 infers `forwardRef<T, P={}>` props as `{}` for plain-JS destructured
// components, so TSX usage with children/className/etc fails with
// `IntrinsicAttributes & RefAttributes<any>`. Declaring these modules `any`-typed
// restores the pre-upgrade (React 18 types) behavior without touching runtime code.

declare module "@/components/ui/alert-dialog" {
  export const AlertDialog: any;
  export const AlertDialogPortal: any;
  export const AlertDialogOverlay: any;
  export const AlertDialogTrigger: any;
  export const AlertDialogContent: any;
  export const AlertDialogHeader: any;
  export const AlertDialogFooter: any;
  export const AlertDialogTitle: any;
  export const AlertDialogDescription: any;
  export const AlertDialogAction: any;
  export const AlertDialogCancel: any;
}

declare module "@/components/ui/badge" {
  export const Badge: any;
  export const badgeVariants: any;
}

declare module "@/components/ui/button" {
  export const Button: any;
  export const buttonVariants: any;
}

declare module "@/components/ui/card" {
  export const Card: any;
  export const CardHeader: any;
  export const CardFooter: any;
  export const CardTitle: any;
  export const CardDescription: any;
  export const CardContent: any;
}

declare module "@/components/ui/command" {
  export const Command: any;
  export const CommandDialog: any;
  export const CommandInput: any;
  export const CommandList: any;
  export const CommandEmpty: any;
  export const CommandGroup: any;
  export const CommandItem: any;
  export const CommandShortcut: any;
  export const CommandSeparator: any;
}

declare module "@/components/ui/dialog" {
  export const Dialog: any;
  export const DialogPortal: any;
  export const DialogOverlay: any;
  export const DialogTrigger: any;
  export const DialogClose: any;
  export const DialogContent: any;
  export const DialogHeader: any;
  export const DialogFooter: any;
  export const DialogTitle: any;
  export const DialogDescription: any;
}

declare module "@/components/ui/dropdown-menu" {
  export const DropdownMenu: any;
  export const DropdownMenuTrigger: any;
  export const DropdownMenuContent: any;
  export const DropdownMenuItem: any;
  export const DropdownMenuCheckboxItem: any;
  export const DropdownMenuRadioItem: any;
  export const DropdownMenuLabel: any;
  export const DropdownMenuSeparator: any;
  export const DropdownMenuShortcut: any;
  export const DropdownMenuGroup: any;
  export const DropdownMenuPortal: any;
  export const DropdownMenuSub: any;
  export const DropdownMenuSubContent: any;
  export const DropdownMenuSubTrigger: any;
  export const DropdownMenuRadioGroup: any;
}

declare module "@/components/ui/input" {
  export const Input: any;
}

declare module "@/components/ui/popover" {
  export const Popover: any;
  export const PopoverTrigger: any;
  export const PopoverContent: any;
  export const PopoverAnchor: any;
}

declare module "@/components/ui/select" {
  export const Select: any;
  export const SelectGroup: any;
  export const SelectValue: any;
  export const SelectTrigger: any;
  export const SelectContent: any;
  export const SelectLabel: any;
  export const SelectItem: any;
  export const SelectSeparator: any;
  export const SelectScrollUpButton: any;
  export const SelectScrollDownButton: any;
}

declare module "@/components/ui/slider" {
  export const Slider: any;
}

declare module "@/components/ui/switch" {
  export const Switch: any;
}

declare module "@/components/ui/tabs" {
  export const Tabs: any;
  export const TabsList: any;
  export const TabsTrigger: any;
  export const TabsContent: any;
}

declare module "@/components/ui/textarea" {
  export const Textarea: any;
}

declare module "@/components/ui/table" {
  export const Table: any;
  export const TableHeader: any;
  export const TableBody: any;
  export const TableFooter: any;
  export const TableRow: any;
  export const TableHead: any;
  export const TableCell: any;
  export const TableCaption: any;
}

declare module "@/components/ui/progress" {
  export const Progress: any;
}

declare module "@/components/ui/checkbox" {
  export const Checkbox: any;
}

declare module "@/components/ui/label" {
  export const Label: any;
}

declare module "@/components/ui/separator" {
  export const Separator: any;
}

declare module "@/components/ui/scroll-area" {
  export const ScrollArea: any;
  export const ScrollBar: any;
}
