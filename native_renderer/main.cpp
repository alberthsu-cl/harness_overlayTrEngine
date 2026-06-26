#include <Windows.h>
#include <d3d11.h>
#include <wincodec.h>
#include <atlbase.h>

#include <algorithm>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <string>
#include <vector>

#include "IOverlayTrEngine.h"
#include "DSPColorSpaceUtility/IDSPColorSpaceUtility_Def.h"
#include "DBGSrc/D3DDumpUtil.h"
#include "SavePNGUtil.h"
#include "rapidjson/document.h"

namespace fs = std::filesystem;

namespace
{
struct RenderRequest
{
    fs::path repoRoot;
    fs::path outputDir;
    fs::path sourceA;
    fs::path sourceB;
    std::string fxId;
    UINT width = 0;
    UINT height = 0;
    UINT fps = 0;
    UINT frameCount = 0;
};

std::wstring Utf8ToWide(const std::string& value)
{
    if (value.empty())
        return std::wstring();

    const int size = MultiByteToWideChar(CP_UTF8, 0, value.c_str(), -1, nullptr, 0);
    std::wstring wide(size - 1, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, value.c_str(), -1, wide.data(), size);
    return wide;
}

std::string ToLower(std::string value)
{
    std::transform(value.begin(), value.end(), value.begin(), [](unsigned char ch) {
        return static_cast<char>(std::tolower(ch));
    });
    return value;
}

bool IsSupportedImageFile(const fs::path& path)
{
    if (!path.has_extension())
        return false;

    const std::string extension = ToLower(path.extension().string());
    return extension == ".png" || extension == ".jpg" || extension == ".jpeg" || extension == ".bmp" || extension == ".tif" || extension == ".tiff";
}

HRESULT ReadTextFile(const fs::path& filePath, std::string& contents)
{
    std::ifstream stream(filePath, std::ios::binary);
    if (!stream)
        return HRESULT_FROM_WIN32(ERROR_FILE_NOT_FOUND);

    contents.assign(std::istreambuf_iterator<char>(stream), std::istreambuf_iterator<char>());
    return S_OK;
}

HRESULT ParseRenderRequest(const fs::path& requestPath, RenderRequest& request)
{
    std::string rawJson;
    HRESULT hr = ReadTextFile(requestPath, rawJson);
    if (FAILED(hr))
        return hr;

    rapidjson::Document document;
    document.Parse(rawJson.c_str());
    if (document.HasParseError())
        return E_FAIL;

    if (!document.HasMember("repo_root") || !document["repo_root"].IsString())
        return E_INVALIDARG;
    if (!document.HasMember("output_dir") || !document["output_dir"].IsString())
        return E_INVALIDARG;
    if (!document.HasMember("job") || !document["job"].IsObject())
        return E_INVALIDARG;

    const auto& job = document["job"];
    if (!job.HasMember("inputs") || !job["inputs"].IsObject())
        return E_INVALIDARG;
    if (!job.HasMember("effect") || !job["effect"].IsObject())
        return E_INVALIDARG;
    if (!job.HasMember("render") || !job["render"].IsObject())
        return E_INVALIDARG;

    const auto& inputs = job["inputs"];
    const auto& effect = job["effect"];
    const auto& render = job["render"];

    if (!inputs.HasMember("source_a") || !inputs["source_a"].IsString())
        return E_INVALIDARG;
    if (!inputs.HasMember("source_b") || !inputs["source_b"].IsString())
        return E_INVALIDARG;
    if (!effect.HasMember("fx_id") || !effect["fx_id"].IsString())
        return E_INVALIDARG;
    if (!render.HasMember("width") || !render["width"].IsUint())
        return E_INVALIDARG;
    if (!render.HasMember("height") || !render["height"].IsUint())
        return E_INVALIDARG;
    if (!render.HasMember("fps") || !render["fps"].IsUint())
        return E_INVALIDARG;
    if (!render.HasMember("frame_count") || !render["frame_count"].IsUint())
        return E_INVALIDARG;

    request.repoRoot = fs::path(Utf8ToWide(document["repo_root"].GetString()));
    request.outputDir = fs::path(Utf8ToWide(document["output_dir"].GetString()));
    request.sourceA = fs::path(Utf8ToWide(inputs["source_a"].GetString()));
    request.sourceB = fs::path(Utf8ToWide(inputs["source_b"].GetString()));
    request.fxId = effect["fx_id"].GetString();
    request.width = render["width"].GetUint();
    request.height = render["height"].GetUint();
    request.fps = render["fps"].GetUint();
    request.frameCount = render["frame_count"].GetUint();

    if (request.sourceA.is_relative())
        request.sourceA = fs::weakly_canonical(request.repoRoot / request.sourceA);
    if (request.sourceB.is_relative())
        request.sourceB = fs::weakly_canonical(request.repoRoot / request.sourceB);
    if (request.outputDir.is_relative())
        request.outputDir = fs::weakly_canonical(request.repoRoot / request.outputDir);

    return S_OK;
}

std::vector<fs::path> EnumerateInputFrames(const fs::path& source)
{
    std::vector<fs::path> frames;

    if (!fs::exists(source))
        return frames;

    if (fs::is_regular_file(source))
    {
        if (IsSupportedImageFile(source))
            frames.push_back(source);
        return frames;
    }

    if (!fs::is_directory(source))
        return frames;

    for (const auto& entry : fs::directory_iterator(source))
    {
        if (entry.is_regular_file() && IsSupportedImageFile(entry.path()))
            frames.push_back(entry.path());
    }

    std::sort(frames.begin(), frames.end());
    return frames;
}

HRESULT CreateD3DDevice(CComPtr<ID3D11Device>& device, CComPtr<ID3D11DeviceContext>& context)
{
    constexpr D3D_FEATURE_LEVEL featureLevels[] = {
        D3D_FEATURE_LEVEL_11_1,
        D3D_FEATURE_LEVEL_11_0,
        D3D_FEATURE_LEVEL_10_1,
        D3D_FEATURE_LEVEL_10_0,
    };

    const UINT flags = D3D11_CREATE_DEVICE_BGRA_SUPPORT;
    D3D_FEATURE_LEVEL featureLevel = D3D_FEATURE_LEVEL_11_0;

    HRESULT hr = D3D11CreateDevice(
        nullptr,
        D3D_DRIVER_TYPE_HARDWARE,
        nullptr,
        flags,
        featureLevels,
        ARRAYSIZE(featureLevels),
        D3D11_SDK_VERSION,
        &device,
        &featureLevel,
        &context);

    if (SUCCEEDED(hr))
        return hr;

    return D3D11CreateDevice(
        nullptr,
        D3D_DRIVER_TYPE_WARP,
        nullptr,
        flags,
        featureLevels,
        ARRAYSIZE(featureLevels),
        D3D11_SDK_VERSION,
        &device,
        &featureLevel,
        &context);
}

HRESULT LoadBitmapBGRA(
    IWICImagingFactory* factory,
    const fs::path& filePath,
    UINT outputWidth,
    UINT outputHeight,
    std::vector<BYTE>& buffer)
{
    if (!factory)
        return E_INVALIDARG;

    CComPtr<IWICBitmapDecoder> decoder;
    HRESULT hr = factory->CreateDecoderFromFilename(
        filePath.c_str(),
        nullptr,
        GENERIC_READ,
        WICDecodeMetadataCacheOnLoad,
        &decoder);
    if (FAILED(hr))
        return hr;

    CComPtr<IWICBitmapFrameDecode> source;
    hr = decoder->GetFrame(0, &source);
    if (FAILED(hr))
        return hr;

    CComPtr<IWICFormatConverter> converter;
    hr = factory->CreateFormatConverter(&converter);
    if (FAILED(hr))
        return hr;

    hr = converter->Initialize(
        source,
        GUID_WICPixelFormat32bppBGRA,
        WICBitmapDitherTypeNone,
        nullptr,
        0.0f,
        WICBitmapPaletteTypeMedianCut);
    if (FAILED(hr))
        return hr;

    UINT inputWidth = 0;
    UINT inputHeight = 0;
    hr = source->GetSize(&inputWidth, &inputHeight);
    if (FAILED(hr))
        return hr;

    buffer.resize(static_cast<size_t>(outputWidth) * outputHeight * 4);

    if (inputWidth == outputWidth && inputHeight == outputHeight)
    {
        WICRect rect{0, 0, static_cast<INT>(outputWidth), static_cast<INT>(outputHeight)};
        return converter->CopyPixels(&rect, outputWidth * 4, outputWidth * outputHeight * 4, buffer.data());
    }

    CComPtr<IWICBitmapScaler> scaler;
    hr = factory->CreateBitmapScaler(&scaler);
    if (FAILED(hr))
        return hr;

    hr = scaler->Initialize(converter, outputWidth, outputHeight, WICBitmapInterpolationModeFant);
    if (FAILED(hr))
        return hr;

    WICRect rect{0, 0, static_cast<INT>(outputWidth), static_cast<INT>(outputHeight)};
    return scaler->CopyPixels(&rect, outputWidth * 4, outputWidth * outputHeight * 4, buffer.data());
}

fs::path ResolveEngineDllPath(const fs::path& repoRoot)
{
    const std::vector<fs::path> candidates = {
        repoRoot / L"overlaytrengine" / L"x64" / L"Debug" / L"OverlayTrEngine.dll",
        repoRoot / L"overlaytrengine" / L"x64" / L"Release" / L"OverlayTrEngine.dll",
        fs::current_path() / L"OverlayTrEngine.dll",
    };

    for (const auto& candidate : candidates)
    {
        if (fs::exists(candidate))
            return candidate;
    }

    return fs::path();
}

std::wstring HrMessage(HRESULT hr)
{
    LPWSTR buffer = nullptr;
    const DWORD length = FormatMessageW(
        FORMAT_MESSAGE_ALLOCATE_BUFFER | FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS,
        nullptr,
        static_cast<DWORD>(hr),
        MAKELANGID(LANG_NEUTRAL, SUBLANG_DEFAULT),
        reinterpret_cast<LPWSTR>(&buffer),
        0,
        nullptr);

    std::wstring message = length > 0 ? buffer : L"Unknown error";
    if (buffer)
        LocalFree(buffer);
    return message;
}

class OverlayEngineSession
{
public:
    ~OverlayEngineSession()
    {
        Cleanup();
    }

    HRESULT Initialize(const fs::path& engineDllPath, const std::string& fxId)
    {
        Cleanup();

        const fs::path engineDir = engineDllPath.parent_path();
        SetDllDirectoryW(engineDir.c_str());

        m_module = LoadLibraryW(engineDllPath.c_str());
        if (!m_module)
            return HRESULT_FROM_WIN32(GetLastError());

        const auto getInfoManager = reinterpret_cast<PFNGetOverlayInfoManager>(GetProcAddress(m_module, "GetOverlayInfoManager"));
        const auto getEngine2 = reinterpret_cast<PFNGetOverlayTrEngine2>(GetProcAddress(m_module, "GetOverlayTrEngine2"));
        m_releaseInfoManager = reinterpret_cast<PFNReleaseOverlayInfoManager>(GetProcAddress(m_module, "ReleaseOverlayInfoManager"));
        m_releaseEngine = reinterpret_cast<PFNReleaseOverlayTrEngine>(GetProcAddress(m_module, "ReleaseOverlayTrEngine"));

        if (!getInfoManager || !getEngine2 || !m_releaseInfoManager || !m_releaseEngine)
            return E_FAIL;

        m_infoManager = getInfoManager();
        m_engine = getEngine2();
        if (!m_infoManager || !m_engine)
            return E_FAIL;

        HRESULT hr = m_engine->Initialize(m_infoManager);
        if (FAILED(hr))
            return hr;

        hr = m_engine->SetWorkingColorSpace(DSPColorSpaceUtility::SDR_709);
        if (FAILED(hr))
            return hr;

        OverlayTr::FadeInfo fadeInfo;
        fadeInfo.fDuration = 1.0f;
        strncpy_s(fadeInfo.szFxID, fxId.c_str(), _MAX_PATH);
        return m_infoManager->SetFadeInfo(fadeInfo);
    }

    HRESULT RenderFrame(
        ID3D11Device* device,
        ID3D11DeviceContext* context,
        const std::vector<BYTE>& sourceA,
        const std::vector<BYTE>& sourceB,
        UINT width,
        UINT height,
        float progress,
        const fs::path& outputPath)
    {
        if (!m_engine || !device || !context)
            return E_UNEXPECTED;

        CComPtr<ID3D11Texture2D> textureA;
        CComPtr<ID3D11Texture2D> textureB;
        CComPtr<ID3D11Texture2D> textureOut;

        HRESULT hr = CreateTexture2D(
            device,
            width,
            height,
            D3D11_BIND_SHADER_RESOURCE | D3D11_BIND_RENDER_TARGET,
            0,
            DXGI_FORMAT_B8G8R8A8_UNORM,
            D3D11_USAGE_DEFAULT,
            const_cast<BYTE*>(sourceA.data()),
            width * 4,
            &textureA);
        if (FAILED(hr))
            return hr;

        hr = CreateTexture2D(
            device,
            width,
            height,
            D3D11_BIND_SHADER_RESOURCE | D3D11_BIND_RENDER_TARGET,
            0,
            DXGI_FORMAT_B8G8R8A8_UNORM,
            D3D11_USAGE_DEFAULT,
            const_cast<BYTE*>(sourceB.data()),
            width * 4,
            &textureB);
        if (FAILED(hr))
            return hr;

        hr = CreateTexture2D(
            device,
            width,
            height,
            D3D11_BIND_SHADER_RESOURCE | D3D11_BIND_RENDER_TARGET,
            0,
            DXGI_FORMAT_B8G8R8A8_UNORM,
            D3D11_USAGE_DEFAULT,
            nullptr,
            0,
            &textureOut);
        if (FAILED(hr))
            return hr;

        OverlayTr::OverlayTrBuffer inputA;
        inputA.enType = OverlayTr::BT_InputA;
        inputA.pTx = textureA;
        hr = m_engine->SetBuffer(&inputA);
        if (FAILED(hr))
            return hr;

        OverlayTr::OverlayTrBuffer inputB;
        inputB.enType = OverlayTr::BT_InputB;
        inputB.pTx = textureB;
        hr = m_engine->SetBuffer(&inputB);
        if (FAILED(hr))
            return hr;

        OverlayTr::OverlayTrBuffer output;
        output.enType = OverlayTr::BT_Output;
        output.pTx = textureOut;
        hr = m_engine->SetBuffer(&output);
        if (FAILED(hr))
            return hr;

        hr = m_engine->Render(progress);
        if (FAILED(hr))
            return hr;

        std::unique_ptr<BYTE[]> bytes(DumpTexture2DData(device, context, textureOut));
        if (!bytes)
            return E_FAIL;

        return savePNGImage(outputPath.c_str(), GUID_WICPixelFormat32bppBGRA, bytes.get(), width, height, width * 4);
    }

private:
    void Cleanup()
    {
        if (m_engine)
        {
            m_engine->Uninitialize();
        }

        if (m_releaseEngine && m_engine)
            m_releaseEngine(m_engine);
        if (m_releaseInfoManager && m_infoManager)
            m_releaseInfoManager(m_infoManager);
        if (m_module)
            FreeLibrary(m_module);

        m_engine = nullptr;
        m_infoManager = nullptr;
        m_releaseEngine = nullptr;
        m_releaseInfoManager = nullptr;
        m_module = nullptr;
    }

    HMODULE m_module = nullptr;
    IOverlayInfoManager* m_infoManager = nullptr;
    IOverlayTrEngine2* m_engine = nullptr;
    PFNReleaseOverlayInfoManager m_releaseInfoManager = nullptr;
    PFNReleaseOverlayTrEngine m_releaseEngine = nullptr;
};

void PrintUsage()
{
    std::wcerr << L"Usage: OverlayTrHarnessRenderer.exe --request <render_request.json>" << std::endl;
}

} // namespace

int wmain(int argc, wchar_t* argv[])
{
    if (argc != 3 || wcscmp(argv[1], L"--request") != 0)
    {
        PrintUsage();
        return 2;
    }

    const fs::path requestPath = argv[2];

    HRESULT hr = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    const bool comInitialized = SUCCEEDED(hr) || hr == RPC_E_CHANGED_MODE;
    if (!comInitialized)
    {
        std::wcerr << L"Failed to initialize COM: " << HrMessage(hr) << std::endl;
        return 1;
    }

    int exitCode = 0;
    {
        RenderRequest request;
        hr = ParseRenderRequest(requestPath, request);
        if (FAILED(hr))
        {
            std::wcerr << L"Failed to parse render request: " << HrMessage(hr) << std::endl;
            exitCode = 1;
            goto cleanup;
        }

        const fs::path engineDllPath = ResolveEngineDllPath(request.repoRoot);
        if (engineDllPath.empty())
        {
            std::wcerr << L"Could not locate OverlayTrEngine.dll under the repository root." << std::endl;
            exitCode = 1;
            goto cleanup;
        }

        const auto sourceAFrames = EnumerateInputFrames(request.sourceA);
        const auto sourceBFrames = EnumerateInputFrames(request.sourceB);
        if (sourceAFrames.empty() || sourceBFrames.empty())
        {
            std::wcerr << L"Input sources must be image files or folders containing images." << std::endl;
            exitCode = 1;
            goto cleanup;
        }

        std::error_code directoryError;
        fs::create_directories(request.outputDir, directoryError);
        if (directoryError)
        {
            std::wcerr << L"Failed to create output directory: " << request.outputDir << std::endl;
            exitCode = 1;
            goto cleanup;
        }

        CComPtr<IWICImagingFactory> wicFactory;
        hr = CoCreateInstance(
            CLSID_WICImagingFactory,
            nullptr,
            CLSCTX_INPROC_SERVER,
            IID_PPV_ARGS(&wicFactory));
        if (FAILED(hr))
        {
            std::wcerr << L"Failed to create WIC imaging factory: " << HrMessage(hr) << std::endl;
            exitCode = 1;
            goto cleanup;
        }

        CComPtr<ID3D11Device> device;
        CComPtr<ID3D11DeviceContext> context;
        hr = CreateD3DDevice(device, context);
        if (FAILED(hr))
        {
            std::wcerr << L"Failed to create D3D11 device: " << HrMessage(hr) << std::endl;
            exitCode = 1;
            goto cleanup;
        }

        OverlayEngineSession session;
        hr = session.Initialize(engineDllPath, request.fxId);
        if (FAILED(hr))
        {
            std::wcerr << L"Failed to initialize overlay engine: " << HrMessage(hr) << std::endl;
            exitCode = 1;
            goto cleanup;
        }

        std::vector<BYTE> bufferA;
        std::vector<BYTE> bufferB;

        for (UINT frameIndex = 0; frameIndex < request.frameCount; ++frameIndex)
        {
            const fs::path& frameA = sourceAFrames[std::min<size_t>(frameIndex, sourceAFrames.size() - 1)];
            const fs::path& frameB = sourceBFrames[std::min<size_t>(frameIndex, sourceBFrames.size() - 1)];

            hr = LoadBitmapBGRA(wicFactory, frameA, request.width, request.height, bufferA);
            if (FAILED(hr))
            {
                std::wcerr << L"Failed to load source A frame: " << frameA << L" - " << HrMessage(hr) << std::endl;
                exitCode = 1;
                goto cleanup;
            }

            hr = LoadBitmapBGRA(wicFactory, frameB, request.width, request.height, bufferB);
            if (FAILED(hr))
            {
                std::wcerr << L"Failed to load source B frame: " << frameB << L" - " << HrMessage(hr) << std::endl;
                exitCode = 1;
                goto cleanup;
            }

            const float progress = request.frameCount > 1
                ? static_cast<float>(frameIndex) / static_cast<float>(request.frameCount - 1)
                : 1.0f;

            wchar_t fileName[64]{};
            swprintf_s(fileName, L"frame_%04u.png", frameIndex);
            const fs::path outputPath = request.outputDir / fileName;

            hr = session.RenderFrame(device, context, bufferA, bufferB, request.width, request.height, progress, outputPath);
            if (FAILED(hr))
            {
                std::wcerr << L"Render failed at frame " << frameIndex << L": " << HrMessage(hr) << std::endl;
                exitCode = 1;
                goto cleanup;
            }

            std::wcout << L"Rendered " << outputPath << std::endl;
        }
    }

cleanup:
    if (comInitialized && hr != RPC_E_CHANGED_MODE)
        CoUninitialize();

    return exitCode;
}