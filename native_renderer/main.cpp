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
#include "rapidjson/stringbuffer.h"
#include "rapidjson/writer.h"

namespace fs = std::filesystem;

namespace
{
struct RenderRequest
{
    fs::path repoRoot;
    fs::path outputDir;
    fs::path sourceA;
    fs::path sourceB;
    fs::path effectSpecPath;
    std::string fxId;
    UINT width = 0;
    UINT height = 0;
    UINT fps = 0;
    UINT frameCount = 0;
};

struct EffectResolution
{
    std::string effectSource = "builtin";
    std::string resolvedFxId;
    std::string fallbackFxId;
};

struct RenderResult
{
    std::string status = "failed";
    std::string summary;
    std::string errorField;
    std::string errorDetail;
    std::string engineDllPath;
    std::string outputDir;
    std::string effectSource;
    std::string resolvedFxId;
    std::string effectSpecPath;
    UINT framesRequested = 0;
    UINT framesRendered = 0;
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

HRESULT FailValidation(const char* field, const char* detail, std::string& errorField, std::string& errorDetail)
{
    errorField = field;
    errorDetail = detail;
    return E_INVALIDARG;
}

HRESULT ReadStringMember(const rapidjson::Value& value, const char* member, std::string& output, std::string& errorField, std::string& errorDetail)
{
    if (!value.HasMember(member))
        return FailValidation(member, "missing required string field", errorField, errorDetail);
    if (!value[member].IsString())
        return FailValidation(member, "field must be a string", errorField, errorDetail);

    output = value[member].GetString();
    if (output.empty())
        return FailValidation(member, "field must not be empty", errorField, errorDetail);

    return S_OK;
}

HRESULT ReadUintMember(const rapidjson::Value& value, const char* member, UINT& output, std::string& errorField, std::string& errorDetail)
{
    if (!value.HasMember(member))
        return FailValidation(member, "missing required unsigned integer field", errorField, errorDetail);
    if (!value[member].IsUint())
        return FailValidation(member, "field must be an unsigned integer", errorField, errorDetail);

    output = value[member].GetUint();
    if (output == 0)
        return FailValidation(member, "field must be greater than zero", errorField, errorDetail);

    return S_OK;
}

std::string WideToUtf8(const std::wstring& value)
{
    if (value.empty())
        return std::string();

    const int size = WideCharToMultiByte(CP_UTF8, 0, value.c_str(), -1, nullptr, 0, nullptr, nullptr);
    std::string utf8(size - 1, '\0');
    WideCharToMultiByte(CP_UTF8, 0, value.c_str(), -1, utf8.data(), size, nullptr, nullptr);
    return utf8;
}

void WriteRendererResult(const fs::path& resultPath, const RenderResult& result)
{
    rapidjson::Document document;
    document.SetObject();
    auto& allocator = document.GetAllocator();

    document.AddMember("status", rapidjson::Value(result.status.c_str(), allocator), allocator);
    document.AddMember("summary", rapidjson::Value(result.summary.c_str(), allocator), allocator);
    document.AddMember("error_field", rapidjson::Value(result.errorField.c_str(), allocator), allocator);
    document.AddMember("error_detail", rapidjson::Value(result.errorDetail.c_str(), allocator), allocator);
    document.AddMember("engine_dll_path", rapidjson::Value(result.engineDllPath.c_str(), allocator), allocator);
    document.AddMember("output_dir", rapidjson::Value(result.outputDir.c_str(), allocator), allocator);
    document.AddMember("effect_source", rapidjson::Value(result.effectSource.c_str(), allocator), allocator);
    document.AddMember("resolved_fx_id", rapidjson::Value(result.resolvedFxId.c_str(), allocator), allocator);
    document.AddMember("effect_spec_path", rapidjson::Value(result.effectSpecPath.c_str(), allocator), allocator);
    document.AddMember("frames_requested", result.framesRequested, allocator);
    document.AddMember("frames_rendered", result.framesRendered, allocator);

    rapidjson::StringBuffer buffer;
    rapidjson::Writer<rapidjson::StringBuffer> writer(buffer);
    document.Accept(writer);

    std::ofstream stream(resultPath, std::ios::binary | std::ios::trunc);
    if (!stream)
        return;

    stream << buffer.GetString() << std::endl;
}

HRESULT ParseRenderRequest(const fs::path& requestPath, RenderRequest& request, std::string& errorField, std::string& errorDetail)
{
    std::string rawJson;
    HRESULT hr = ReadTextFile(requestPath, rawJson);
    if (FAILED(hr))
    {
        errorField = "request_file";
        errorDetail = "request file could not be opened";
        return hr;
    }

    rapidjson::Document document;
    document.Parse(rawJson.c_str());
    if (document.HasParseError())
        return FailValidation("request_json", "JSON parse error", errorField, errorDetail);

    if (!document.HasMember("repo_root") || !document["repo_root"].IsString())
        return FailValidation("repo_root", "missing or invalid repo_root", errorField, errorDetail);
    if (!document.HasMember("output_dir") || !document["output_dir"].IsString())
        return FailValidation("output_dir", "missing or invalid output_dir", errorField, errorDetail);
    if (!document.HasMember("job") || !document["job"].IsObject())
        return FailValidation("job", "missing or invalid job object", errorField, errorDetail);

    const auto& job = document["job"];
    if (!job.HasMember("inputs") || !job["inputs"].IsObject())
        return FailValidation("job.inputs", "missing or invalid inputs object", errorField, errorDetail);
    if (!job.HasMember("effect") || !job["effect"].IsObject())
        return FailValidation("job.effect", "missing or invalid effect object", errorField, errorDetail);
    if (!job.HasMember("render") || !job["render"].IsObject())
        return FailValidation("job.render", "missing or invalid render object", errorField, errorDetail);

    const auto& inputs = job["inputs"];
    const auto& effect = job["effect"];
    const auto& render = job["render"];

    std::string repoRoot;
    std::string outputDir;
    std::string sourceA;
    std::string sourceB;

    hr = ReadStringMember(document, "repo_root", repoRoot, errorField, errorDetail);
    if (FAILED(hr))
        return hr;
    hr = ReadStringMember(document, "output_dir", outputDir, errorField, errorDetail);
    if (FAILED(hr))
        return hr;
    hr = ReadStringMember(inputs, "source_a", sourceA, errorField, errorDetail);
    if (FAILED(hr))
    {
        errorField = "job.inputs.source_a";
        return hr;
    }
    hr = ReadStringMember(inputs, "source_b", sourceB, errorField, errorDetail);
    if (FAILED(hr))
    {
        errorField = "job.inputs.source_b";
        return hr;
    }
    hr = ReadStringMember(effect, "fx_id", request.fxId, errorField, errorDetail);
    if (FAILED(hr))
    {
        errorField = "job.effect.fx_id";
        return hr;
    }
    hr = ReadUintMember(render, "width", request.width, errorField, errorDetail);
    if (FAILED(hr))
    {
        errorField = "job.render.width";
        return hr;
    }
    hr = ReadUintMember(render, "height", request.height, errorField, errorDetail);
    if (FAILED(hr))
    {
        errorField = "job.render.height";
        return hr;
    }
    hr = ReadUintMember(render, "fps", request.fps, errorField, errorDetail);
    if (FAILED(hr))
    {
        errorField = "job.render.fps";
        return hr;
    }
    hr = ReadUintMember(render, "frame_count", request.frameCount, errorField, errorDetail);
    if (FAILED(hr))
    {
        errorField = "job.render.frame_count";
        return hr;
    }

    request.repoRoot = fs::path(Utf8ToWide(repoRoot));
    request.outputDir = fs::path(Utf8ToWide(outputDir));
    request.sourceA = fs::path(Utf8ToWide(sourceA));
    request.sourceB = fs::path(Utf8ToWide(sourceB));

    if (effect.HasMember("effect_spec") && effect["effect_spec"].IsString())
    {
        request.effectSpecPath = fs::path(Utf8ToWide(effect["effect_spec"].GetString()));
    }

    if (request.width < 16 || request.height < 16)
        return FailValidation("job.render", "width and height must be at least 16", errorField, errorDetail);
    if ((request.width % 2) != 0 || (request.height % 2) != 0)
        return FailValidation("job.render", "width and height must be even numbers", errorField, errorDetail);

    if (request.sourceA.is_relative())
        request.sourceA = fs::weakly_canonical(request.repoRoot / request.sourceA);
    if (request.sourceB.is_relative())
        request.sourceB = fs::weakly_canonical(request.repoRoot / request.sourceB);
    if (request.outputDir.is_relative())
        request.outputDir = fs::weakly_canonical(request.repoRoot / request.outputDir);
    if (!request.effectSpecPath.empty() && request.effectSpecPath.is_relative())
        request.effectSpecPath = fs::weakly_canonical(request.repoRoot / request.effectSpecPath);

    if (!fs::exists(request.repoRoot))
        return FailValidation("repo_root", "repo_root does not exist", errorField, errorDetail);
    if (!request.effectSpecPath.empty() && !fs::exists(request.effectSpecPath))
        return FailValidation("job.effect.effect_spec", "effect_spec path does not exist", errorField, errorDetail);

    return S_OK;
}

HRESULT ResolveEffect(const RenderRequest& request, EffectResolution& resolution, std::string& errorField, std::string& errorDetail)
{
    resolution.resolvedFxId = request.fxId;

    if (request.effectSpecPath.empty())
        return S_OK;

    std::string rawJson;
    HRESULT hr = ReadTextFile(request.effectSpecPath, rawJson);
    if (FAILED(hr))
    {
        errorField = "job.effect.effect_spec";
        errorDetail = "effect_spec file could not be opened";
        return hr;
    }

    rapidjson::Document document;
    document.Parse(rawJson.c_str());
    if (document.HasParseError())
        return FailValidation("job.effect.effect_spec", "effect_spec JSON parse error", errorField, errorDetail);

    if (!document.IsObject())
        return FailValidation("job.effect.effect_spec", "effect_spec root must be an object", errorField, errorDetail);

    if (!document.HasMember("runtime") || !document["runtime"].IsObject())
        return FailValidation("job.effect.effect_spec.runtime", "effect_spec must contain a runtime object", errorField, errorDetail);

    const auto& runtime = document["runtime"];
    std::string effectSource;
    hr = ReadStringMember(runtime, "effect_source", effectSource, errorField, errorDetail);
    if (FAILED(hr))
    {
        errorField = "job.effect.effect_spec.runtime.effect_source";
        return hr;
    }

    resolution.effectSource = effectSource;

    if (runtime.HasMember("fallback_fx_id") && runtime["fallback_fx_id"].IsString())
        resolution.fallbackFxId = runtime["fallback_fx_id"].GetString();

    if (effectSource == "builtin")
    {
        std::string builtinFxId;
        hr = ReadStringMember(runtime, "fx_id", builtinFxId, errorField, errorDetail);
        if (FAILED(hr))
        {
            errorField = "job.effect.effect_spec.runtime.fx_id";
            return hr;
        }
        resolution.resolvedFxId = builtinFxId;
        return S_OK;
    }

    if (effectSource == "generated")
    {
        if (runtime.HasMember("registered_fx_id") && runtime["registered_fx_id"].IsString())
        {
            resolution.resolvedFxId = runtime["registered_fx_id"].GetString();
            if (!resolution.resolvedFxId.empty())
                return S_OK;
        }

        if (!resolution.fallbackFxId.empty())
        {
            resolution.resolvedFxId = resolution.fallbackFxId;
            return S_OK;
        }

        return FailValidation(
            "job.effect.effect_spec.runtime",
            "generated effect specs must provide runtime.registered_fx_id or runtime.fallback_fx_id",
            errorField,
            errorDetail);
    }

    return FailValidation(
        "job.effect.effect_spec.runtime.effect_source",
        "effect_source must be 'builtin' or 'generated'",
        errorField,
        errorDetail);
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
    const fs::path resultPath = requestPath.parent_path() / L"renderer_result.json";
    RenderResult result;

    HRESULT hr = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    const bool comInitialized = SUCCEEDED(hr) || hr == RPC_E_CHANGED_MODE;
    if (!comInitialized)
    {
        result.summary = "Failed to initialize COM";
        result.errorField = "runtime";
        result.errorDetail = WideToUtf8(HrMessage(hr));
        WriteRendererResult(resultPath, result);
        std::wcerr << L"Failed to initialize COM: " << HrMessage(hr) << std::endl;
        return 1;
    }

    int exitCode = 0;
    {
        RenderRequest request;
        std::string errorField;
        std::string errorDetail;
        hr = ParseRenderRequest(requestPath, request, errorField, errorDetail);
        if (FAILED(hr))
        {
            result.summary = "Render request validation failed";
            result.errorField = errorField;
            result.errorDetail = errorDetail.empty() ? WideToUtf8(HrMessage(hr)) : errorDetail;
            WriteRendererResult(resultPath, result);
            std::wcerr << L"Failed to parse render request: " << Utf8ToWide(result.errorField) << L" - " << Utf8ToWide(result.errorDetail) << std::endl;
            exitCode = 1;
            goto cleanup;
        }

        result.outputDir = WideToUtf8(request.outputDir.wstring());
        result.effectSpecPath = WideToUtf8(request.effectSpecPath.wstring());
        result.framesRequested = request.frameCount;

        EffectResolution effectResolution;
        hr = ResolveEffect(request, effectResolution, errorField, errorDetail);
        if (FAILED(hr))
        {
            result.summary = "Effect specification resolution failed";
            result.errorField = errorField;
            result.errorDetail = errorDetail.empty() ? WideToUtf8(HrMessage(hr)) : errorDetail;
            WriteRendererResult(resultPath, result);
            std::wcerr << L"Failed to resolve effect spec: " << Utf8ToWide(result.errorField) << L" - " << Utf8ToWide(result.errorDetail) << std::endl;
            exitCode = 1;
            goto cleanup;
        }
        result.effectSource = effectResolution.effectSource;
        result.resolvedFxId = effectResolution.resolvedFxId;

        const fs::path engineDllPath = ResolveEngineDllPath(request.repoRoot);
        if (engineDllPath.empty())
        {
            result.summary = "OverlayTrEngine.dll could not be located";
            result.errorField = "engine_dll";
            result.errorDetail = "checked repository output folders and current working directory";
            WriteRendererResult(resultPath, result);
            std::wcerr << L"Could not locate OverlayTrEngine.dll under the repository root." << std::endl;
            exitCode = 1;
            goto cleanup;
        }
        result.engineDllPath = WideToUtf8(engineDllPath.wstring());

        const auto sourceAFrames = EnumerateInputFrames(request.sourceA);
        const auto sourceBFrames = EnumerateInputFrames(request.sourceB);
        if (sourceAFrames.empty() || sourceBFrames.empty())
        {
            result.summary = "Input source discovery failed";
            result.errorField = sourceAFrames.empty() ? "job.inputs.source_a" : "job.inputs.source_b";
            result.errorDetail = "input source must be an image file or a folder containing supported images";
            WriteRendererResult(resultPath, result);
            std::wcerr << L"Input sources must be image files or folders containing images." << std::endl;
            exitCode = 1;
            goto cleanup;
        }

        std::error_code directoryError;
        fs::create_directories(request.outputDir, directoryError);
        if (directoryError)
        {
            result.summary = "Failed to create output directory";
            result.errorField = "output_dir";
            result.errorDetail = directoryError.message();
            WriteRendererResult(resultPath, result);
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
            result.summary = "Failed to create WIC imaging factory";
            result.errorField = "runtime";
            result.errorDetail = WideToUtf8(HrMessage(hr));
            WriteRendererResult(resultPath, result);
            std::wcerr << L"Failed to create WIC imaging factory: " << HrMessage(hr) << std::endl;
            exitCode = 1;
            goto cleanup;
        }

        CComPtr<ID3D11Device> device;
        CComPtr<ID3D11DeviceContext> context;
        hr = CreateD3DDevice(device, context);
        if (FAILED(hr))
        {
            result.summary = "Failed to create D3D11 device";
            result.errorField = "runtime";
            result.errorDetail = WideToUtf8(HrMessage(hr));
            WriteRendererResult(resultPath, result);
            std::wcerr << L"Failed to create D3D11 device: " << HrMessage(hr) << std::endl;
            exitCode = 1;
            goto cleanup;
        }

        OverlayEngineSession session;
        hr = session.Initialize(engineDllPath, effectResolution.resolvedFxId);
        if (FAILED(hr))
        {
            result.summary = "Failed to initialize overlay engine";
            result.errorField = "engine_init";
            result.errorDetail = WideToUtf8(HrMessage(hr));
            WriteRendererResult(resultPath, result);
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
                result.summary = "Failed to load source A frame";
                result.errorField = "job.inputs.source_a";
                result.errorDetail = WideToUtf8(frameA.wstring()) + " | " + WideToUtf8(HrMessage(hr));
                WriteRendererResult(resultPath, result);
                std::wcerr << L"Failed to load source A frame: " << frameA << L" - " << HrMessage(hr) << std::endl;
                exitCode = 1;
                goto cleanup;
            }

            hr = LoadBitmapBGRA(wicFactory, frameB, request.width, request.height, bufferB);
            if (FAILED(hr))
            {
                result.summary = "Failed to load source B frame";
                result.errorField = "job.inputs.source_b";
                result.errorDetail = WideToUtf8(frameB.wstring()) + " | " + WideToUtf8(HrMessage(hr));
                WriteRendererResult(resultPath, result);
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
                result.summary = "Frame render failed";
                result.errorField = "render";
                result.errorDetail = std::string("frame=") + std::to_string(frameIndex) + " | " + WideToUtf8(HrMessage(hr));
                WriteRendererResult(resultPath, result);
                std::wcerr << L"Render failed at frame " << frameIndex << L": " << HrMessage(hr) << std::endl;
                exitCode = 1;
                goto cleanup;
            }

            result.framesRendered = frameIndex + 1;

            std::wcout << L"Rendered " << outputPath << std::endl;
        }

        result.status = "succeeded";
        result.summary = "Renderer completed successfully";
        result.errorField.clear();
        result.errorDetail.clear();
        WriteRendererResult(resultPath, result);
    }

cleanup:
    if (comInitialized && hr != RPC_E_CHANGED_MODE)
        CoUninitialize();

    return exitCode;
}