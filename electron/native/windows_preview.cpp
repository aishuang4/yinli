/** Windows 把 IPreviewHandler 嵌进 Electron 窗口，只读，不启动 Office 编辑。 */

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif

#include <string>

#include <napi.h>
#include <objbase.h>
#include <shlwapi.h>
#include <shobjidl.h>
#include <shlobj.h>
#include <windows.h>

#pragma comment(lib, "ole32.lib")
#pragma comment(lib, "oleaut32.lib")
#pragma comment(lib, "shell32.lib")
#pragma comment(lib, "shlwapi.lib")
#pragma comment(lib, "user32.lib")
#pragma comment(lib, "gdi32.lib")
#pragma comment(lib, "uuid.lib")

namespace {

constexpr wchar_t kHostClass[] = L"YinliPreviewHost";
constexpr wchar_t kPreviewHandlerKey[] = L"ShellEx\\{8895B1C6-B41F-4C1C-A562-0D564250836F}";

HWND g_host = nullptr;
IPreviewHandler *g_handler = nullptr;
bool g_com_ready = false;

std::wstring Utf8ToWide(const std::string &utf8) {
  if (utf8.empty()) {
    return std::wstring();
  }
  const int size = MultiByteToWideChar(CP_UTF8, 0, utf8.c_str(), -1, nullptr, 0);
  if (size <= 1) {
    return std::wstring();
  }
  std::wstring wide(static_cast<size_t>(size - 1), L'\0');
  MultiByteToWideChar(CP_UTF8, 0, utf8.c_str(), -1, wide.data(), size);
  return wide;
}

std::wstring FileExtension(const std::wstring &path) {
  const size_t slash = path.find_last_of(L"\\/");
  const size_t dot = path.find_last_of(L'.');
  if (dot == std::wstring::npos || (slash != std::wstring::npos && dot < slash)) {
    return std::wstring();
  }
  return path.substr(dot);
}

bool ReadClsid(const std::wstring &subkey, CLSID *clsid) {
  wchar_t value[128] = {};
  DWORD size = sizeof(value);
  if (RegGetValueW(HKEY_CLASSES_ROOT, subkey.c_str(), nullptr, RRF_RT_REG_SZ, nullptr, value, &size) != ERROR_SUCCESS) {
    return false;
  }
  return SUCCEEDED(CLSIDFromString(value, clsid));
}

bool GetPreviewHandlerClsid(const std::wstring &path, CLSID *clsid) {
  const std::wstring ext = FileExtension(path);
  if (ext.empty() || !clsid) {
    return false;
  }
  if (ReadClsid(ext + L"\\" + kPreviewHandlerKey, clsid)) {
    return true;
  }
  if (ReadClsid(L"SystemFileAssociations\\" + ext + L"\\" + kPreviewHandlerKey, clsid)) {
    return true;
  }
  wchar_t progid[128] = {};
  DWORD size = sizeof(progid);
  if (RegGetValueW(HKEY_CLASSES_ROOT, ext.c_str(), nullptr, RRF_RT_REG_SZ, nullptr, progid, &size) == ERROR_SUCCESS) {
    if (ReadClsid(std::wstring(progid) + L"\\" + kPreviewHandlerKey, clsid)) {
      return true;
    }
  }
  return false;
}

void EnableClipStyles(HWND hwnd) {
  if (!IsWindow(hwnd)) {
    return;
  }
  const LONG_PTR style = GetWindowLongPtrW(hwnd, GWL_STYLE);
  SetWindowLongPtrW(hwnd, GWL_STYLE, style | WS_CLIPCHILDREN | WS_CLIPSIBLINGS);
  HWND child = GetWindow(hwnd, GW_CHILD);
  while (child) {
    wchar_t name[256] = {};
    GetClassNameW(child, name, 256);
    if (wcsstr(name, L"Chrome") || wcsstr(name, L"Intermediate") || wcsstr(name, L"RenderWidget")) {
      const LONG_PTR child_style = GetWindowLongPtrW(child, GWL_STYLE);
      SetWindowLongPtrW(child, GWL_STYLE, child_style | WS_CLIPSIBLINGS);
    }
    child = GetWindow(child, GW_HWNDNEXT);
  }
}

void UnloadHandler() {
  if (g_handler) {
    g_handler->Unload();
    g_handler->Release();
    g_handler = nullptr;
  }
}

void ClosePreview() {
  UnloadHandler();
  if (g_host && IsWindow(g_host)) {
    DestroyWindow(g_host);
  }
  g_host = nullptr;
}

bool EnsureCom() {
  if (g_com_ready) {
    return true;
  }
  const HRESULT hr = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
  if (hr == S_OK || hr == S_FALSE || hr == RPC_E_CHANGED_MODE) {
    g_com_ready = true;
    return true;
  }
  return false;
}

bool InitializeHandler(IPreviewHandler *handler, const std::wstring &path) {
  IInitializeWithFile *init_file = nullptr;
  if (SUCCEEDED(handler->QueryInterface(IID_PPV_ARGS(&init_file))) && init_file) {
    const HRESULT hr = init_file->Initialize(path.c_str(), STGM_READ);
    init_file->Release();
    if (SUCCEEDED(hr)) {
      return true;
    }
  }
  IInitializeWithStream *init_stream = nullptr;
  if (SUCCEEDED(handler->QueryInterface(IID_PPV_ARGS(&init_stream))) && init_stream) {
    IStream *stream = nullptr;
    const HRESULT stream_hr = SHCreateStreamOnFileEx(
      path.c_str(),
      STGM_READ | STGM_SHARE_DENY_NONE,
      0,
      FALSE,
      nullptr,
      &stream
    );
    HRESULT hr = E_FAIL;
    if (SUCCEEDED(stream_hr) && stream) {
      hr = init_stream->Initialize(stream, STGM_READ);
      stream->Release();
    }
    init_stream->Release();
    if (SUCCEEDED(hr)) {
      return true;
    }
  }
  IInitializeWithItem *init_item = nullptr;
  if (SUCCEEDED(handler->QueryInterface(IID_PPV_ARGS(&init_item))) && init_item) {
    IShellItem *item = nullptr;
    HRESULT hr = SHCreateItemFromParsingName(path.c_str(), nullptr, IID_PPV_ARGS(&item));
    if (SUCCEEDED(hr) && item) {
      hr = init_item->Initialize(item, STGM_READ);
      item->Release();
    }
    init_item->Release();
    return SUCCEEDED(hr);
  }
  return false;
}

HWND HostWindow(HWND parent, int x, int y, int width, int height) {
  static bool registered = false;
  if (!registered) {
    WNDCLASSW wc = {};
    wc.lpfnWndProc = DefWindowProcW;
    wc.hInstance = GetModuleHandleW(nullptr);
    wc.lpszClassName = kHostClass;
    wc.hCursor = LoadCursorW(nullptr, IDC_ARROW);
    wc.hbrBackground = reinterpret_cast<HBRUSH>(COLOR_WINDOW + 1);
    RegisterClassW(&wc);
    registered = true;
  }
  if (!g_host || !IsWindow(g_host)) {
    g_host = CreateWindowExW(
      0,
      kHostClass,
      L"",
      WS_CHILD | WS_VISIBLE | WS_CLIPSIBLINGS | WS_CLIPCHILDREN,
      x,
      y,
      width,
      height,
      parent,
      nullptr,
      GetModuleHandleW(nullptr),
      nullptr
    );
  } else {
    SetWindowPos(g_host, HWND_TOP, x, y, width, height, SWP_SHOWWINDOW | SWP_NOACTIVATE);
  }
  return g_host;
}

int ShowPreview(HWND parent, const std::string &utf8_path, int x, int y, int width, int height) {
  if (!IsWindow(parent) || width < 8 || height < 8) {
    return -1;
  }
  const std::wstring path = Utf8ToWide(utf8_path);
  if (path.empty() || GetFileAttributesW(path.c_str()) == INVALID_FILE_ATTRIBUTES) {
    return -3;
  }
  if (!EnsureCom()) {
    return -5;
  }
  EnableClipStyles(parent);
  HWND host = HostWindow(parent, x, y, width, height);
  if (!host) {
    return -2;
  }
  UnloadHandler();
  CLSID clsid = {};
  if (!GetPreviewHandlerClsid(path, &clsid)) {
    return -4;
  }
  HRESULT hr = CoCreateInstance(clsid, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&g_handler));
  if (FAILED(hr) || !g_handler) {
    hr = CoCreateInstance(clsid, nullptr, CLSCTX_LOCAL_SERVER, IID_PPV_ARGS(&g_handler));
  }
  if (FAILED(hr) || !g_handler) {
    g_handler = nullptr;
    return -5;
  }
  if (!InitializeHandler(g_handler, path)) {
    UnloadHandler();
    return -6;
  }
  RECT rect = {0, 0, width, height};
  g_handler->SetWindow(host, &rect);
  hr = g_handler->DoPreview();
  if (FAILED(hr)) {
    UnloadHandler();
    return -7;
  }
  SetWindowPos(host, HWND_TOP, x, y, width, height, SWP_SHOWWINDOW | SWP_NOACTIVATE);
  return 0;
}

int UpdatePreview(int x, int y, int width, int height) {
  if (!g_host || !IsWindow(g_host) || width < 8 || height < 8) {
    return -1;
  }
  SetWindowPos(g_host, HWND_TOP, x, y, width, height, SWP_SHOWWINDOW | SWP_NOACTIVATE);
  if (g_handler) {
    RECT rect = {0, 0, width, height};
    g_handler->SetRect(&rect);
  }
  return 0;
}

HWND HwndFromBuffer(const Napi::Value &value) {
  if (!value.IsBuffer()) {
    return nullptr;
  }
  const Napi::Buffer<uint8_t> buffer = value.As<Napi::Buffer<uint8_t>>();
  if (buffer.ByteLength() >= sizeof(HWND)) {
    return *reinterpret_cast<HWND *>(buffer.Data());
  }
  return nullptr;
}

}  // namespace

static Napi::Value Show(const Napi::CallbackInfo &info) {
  Napi::Env env = info.Env();
  if (info.Length() < 6 || !info[1].IsString()) {
    return Napi::Number::New(env, -1);
  }
  HWND parent = HwndFromBuffer(info[0]);
  const std::string path = info[1].As<Napi::String>().Utf8Value();
  const int x = static_cast<int>(info[2].As<Napi::Number>().DoubleValue());
  const int y = static_cast<int>(info[3].As<Napi::Number>().DoubleValue());
  const int width = static_cast<int>(info[4].As<Napi::Number>().DoubleValue());
  const int height = static_cast<int>(info[5].As<Napi::Number>().DoubleValue());
  return Napi::Number::New(env, ShowPreview(parent, path, x, y, width, height));
}

static Napi::Value Update(const Napi::CallbackInfo &info) {
  Napi::Env env = info.Env();
  if (info.Length() < 4) {
    return Napi::Number::New(env, -1);
  }
  return Napi::Number::New(env, UpdatePreview(
    static_cast<int>(info[0].As<Napi::Number>().DoubleValue()),
    static_cast<int>(info[1].As<Napi::Number>().DoubleValue()),
    static_cast<int>(info[2].As<Napi::Number>().DoubleValue()),
    static_cast<int>(info[3].As<Napi::Number>().DoubleValue())
  ));
}

static Napi::Value Close(const Napi::CallbackInfo &info) {
  ClosePreview();
  return info.Env().Undefined();
}

static Napi::Object Init(Napi::Env env, Napi::Object exports) {
  exports.Set("show", Napi::Function::New(env, Show));
  exports.Set("update", Napi::Function::New(env, Update));
  exports.Set("close", Napi::Function::New(env, Close));
  return exports;
}

NODE_API_MODULE(system_preview, Init)
