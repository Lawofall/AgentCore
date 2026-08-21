import { Button, IconButton } from "@/components/ui";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { SimpleTooltip } from "@/components/ui/tooltip";
import { FolderUp, Loader2, Upload } from "lucide-react";
import { useState } from "react";

/**
 * 云盘标准：工具条只留一颗上传图标，菜单里再拆文件 / 文件夹
 * （两种系统选择器互斥，不能合成一个对话框）。
 */
export function UploadMenu({
  uploading,
  onUploadFiles,
  onUploadFolder,
}: {
  uploading: boolean;
  onUploadFiles: () => void;
  onUploadFolder: () => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <SimpleTooltip label="上传">
        <PopoverTrigger asChild>
          <IconButton
            disabled={uploading}
            aria-label="上传"
            aria-expanded={open}
            aria-haspopup="menu"
          >
            {uploading ? (
              <Loader2 size={14} className="animate-spin" />
            ) : (
              <Upload size={14} />
            )}
          </IconButton>
        </PopoverTrigger>
      </SimpleTooltip>
      <PopoverContent align="start" className="w-44 p-1.5">
        <Button
          variant="ghost"
          onClick={() => {
            setOpen(false);
            onUploadFiles();
          }}
          className="h-auto w-full justify-start px-2.5 py-1.5 text-left text-xs font-medium"
          icon={<Upload size={14} />}
        >
          上传文件
        </Button>
        <Button
          variant="ghost"
          onClick={() => {
            setOpen(false);
            onUploadFolder();
          }}
          className="h-auto w-full justify-start px-2.5 py-1.5 text-left text-xs font-medium"
          icon={<FolderUp size={14} />}
        >
          上传文件夹
        </Button>
      </PopoverContent>
    </Popover>
  );
}
