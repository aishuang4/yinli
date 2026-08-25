; Windows NSIS 卸载时询问是否删除 YINLI 用户数据。
; 只允许删除固定的当前目录和历史兼容目录，默认保留数据。
!include "LogicLib.nsh"
; electron-builder 将 NSIS 警告视为错误；该变量在卸载宏展开后才会被引用。
!pragma warning disable 6001
Var dataCleanupFailed

!macro customUnInstall
  StrCpy $dataCleanupFailed 0
  MessageBox MB_YESNO|MB_DEFBUTTON2|MB_ICONQUESTION \
    "是否删除引力 YINLI 的用户数据？$\r$\n$\r$\n选择“是”将删除对话、Agent、Skill、MCP、Memory、日志、本地配置和 API Key。$\r$\n选择“否”只卸载应用并保留用户数据。" \
    IDNO keep_user_data

  ; 当前稳定目录：Electron userData = %USERPROFILE%\.agent-workstation
  ${If} ${FileExists} "$PROFILE\.agent-workstation"
    RMDir /r "$PROFILE\.agent-workstation"
    ${If} ${FileExists} "$PROFILE\.agent-workstation"
      StrCpy $dataCleanupFailed 1
    ${EndIf}
  ${EndIf}

  ; 历史版本目录：用于兼容早期 Electron 默认 userData 路径。
  ${If} ${FileExists} "$APPDATA\agent-workstation-desktop"
    RMDir /r "$APPDATA\agent-workstation-desktop"
    ${If} ${FileExists} "$APPDATA\agent-workstation-desktop"
      StrCpy $dataCleanupFailed 1
    ${EndIf}
  ${EndIf}

  ${If} $dataCleanupFailed == 1
    MessageBox MB_OK|MB_ICONEXCLAMATION \
      "应用已卸载，但部分用户数据未能删除，请手动检查用户数据目录。"
  ${Else}
    MessageBox MB_OK|MB_ICONINFORMATION \
      "应用已卸载，用户数据已删除。"
  ${EndIf}
  Goto uninstall_done

  keep_user_data:
    MessageBox MB_OK|MB_ICONINFORMATION \
      "应用已卸载，用户数据已保留。重新安装后可以继续使用。"

  uninstall_done:
!macroend
