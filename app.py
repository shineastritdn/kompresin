from flask import Flask, render_template, request, jsonify, send_file
import os
from werkzeug.utils import secure_filename
import zipfile
import magic
from PIL import Image
import io
import tempfile
import traceback
import shutil
import subprocess

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['ALLOWED_EXTENSIONS'] = {'txt', 'pdf', 'png', 'jpg', 'jpeg', 'gif', 'docx', 'csv'}

# Buat folder uploads jika belum ada
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def compress_file(file_path, file_type):
    try:
        if file_type.startswith('image/'):
            img = Image.open(file_path)
            original_size = os.path.getsize(file_path)
            max_width = 800
            min_width = 300
            quality = 35
            min_quality = 15
            target_ratio = 0.5  # 50% dari ukuran asli
            output_data = None
            while True:
                temp_img = img.copy()
                # Resize jika perlu
                if temp_img.size[0] > max_width:
                    w_percent = max_width / float(temp_img.size[0])
                    h_size = int((float(temp_img.size[1]) * float(w_percent)))
                    try:
                        resample_method = Image.Resampling.LANCZOS
                    except AttributeError:
                        resample_method = Image.Resampling.BICUBIC
                    temp_img = temp_img.resize((max_width, h_size), resample=resample_method)
                # Konversi PNG ke JPG
                if temp_img.format == 'PNG' or temp_img.mode in ('RGBA', 'LA'):
                    temp_img = temp_img.convert('RGB')
                output = io.BytesIO()
                temp_img.save(output, format='JPEG', optimize=True, quality=quality)
                output_data = output.getvalue()
                # Cek apakah sudah cukup kecil
                if len(output_data) <= original_size * target_ratio or (quality <= min_quality and max_width <= min_width):
                    break
                # Jika belum, turunkan kualitas dan/atau resize
                if quality > min_quality:
                    quality -= 5
                elif max_width > min_width:
                    max_width -= 100
                else:
                    break
            return output_data
        elif file_type == 'application/pdf' or file_path.lower().endswith('.pdf'):
            # Kompres PDF dengan ghostscript
            original_size = os.path.getsize(file_path)
            target_ratio = 0.5
            compressed_pdf_path = file_path + '_compressed.pdf'
            gs_cmd = [
                'gswin64c',  # atau 'gswin32c' jika 32bit, atau 'gs' di Linux
                '-sDEVICE=pdfwrite',
                '-dCompatibilityLevel=1.4',
                '-dPDFSETTINGS=/ebook',  # bisa diganti /screen untuk lebih kecil
                '-dNOPAUSE',
                '-dQUIET',
                '-dBATCH',
                f'-sOutputFile={compressed_pdf_path}',
                file_path
            ]
            try:
                subprocess.run(gs_cmd, check=True)
                # Cek hasil kompresi
                if os.path.exists(compressed_pdf_path):
                    compressed_size = os.path.getsize(compressed_pdf_path)
                    if compressed_size <= original_size * target_ratio:
                        # Kompresi berhasil, ZIP hasilnya
                        output = io.BytesIO()
                        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as zipf:
                            zipf.write(compressed_pdf_path, os.path.basename(compressed_pdf_path))
                        os.remove(compressed_pdf_path)
                        return output.getvalue()
                    else:
                        # Kompresi ghostscript tidak cukup, pakai hasil ZIP biasa
                        os.remove(compressed_pdf_path)
            except Exception as e:
                print(f"Ghostscript error: {e}")
                if os.path.exists(compressed_pdf_path):
                    os.remove(compressed_pdf_path)
            # Fallback: ZIP biasa
            output = io.BytesIO()
            with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as zipf:
                if os.path.exists(file_path):
                    zipf.write(file_path, os.path.basename(file_path))
                else:
                    raise FileNotFoundError(f"File tidak ditemukan: {file_path}")
            return output.getvalue()
        else:
            # Kompresi file lain menggunakan zip adaptif
            original_size = os.path.getsize(file_path)
            target_ratio = 0.5  # 50% dari ukuran asli
            best_zip = None
            best_zip_size = None
            for _ in range(2):  # Coba 2x, meski ZIP_DEFLATED sudah optimal
                output = io.BytesIO()
                with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    if os.path.exists(file_path):
                        zipf.write(file_path, os.path.basename(file_path))
                    else:
                        raise FileNotFoundError(f"File tidak ditemukan: {file_path}")
                zip_data = output.getvalue()
                if best_zip is None or (best_zip_size is not None and len(zip_data) < best_zip_size):
                    best_zip = zip_data
                    best_zip_size = len(zip_data)
                if len(zip_data) <= original_size * target_ratio:
                    break
            return best_zip
    except Exception as e:
        print(f"Error compressing file: {str(e)}")
        raise

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    temp_file_path = None
    compressed_path = None
    
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'Tidak ada file yang diunggah'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'Tidak ada file yang dipilih'}), 400
        
        if file and allowed_file(file.filename):
            # Buat nama file yang aman
            filename = secure_filename(file.filename)
            temp_file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            
            # Simpan file sementara
            file.save(temp_file_path)
            
            try:
                # Deteksi tipe file
                file_type = magic.from_file(temp_file_path, mime=True)
                
                # Dapatkan ukuran file asli
                original_size = os.path.getsize(temp_file_path)
                
                # Kompres file
                compressed_data = compress_file(temp_file_path, file_type)
                
                # Simpan file terkompresi
                compressed_filename = f"compressed_{filename}.zip"
                compressed_path = os.path.join(app.config['UPLOAD_FOLDER'], compressed_filename)
                
                with open(compressed_path, 'wb') as f:
                    f.write(compressed_data)
                
                # Hapus file asli setelah berhasil dikompresi
                if os.path.exists(temp_file_path):
                    os.remove(temp_file_path)
                
                return jsonify({
                    'success': True,
                    'original_size': original_size,
                    'compressed_size': len(compressed_data),
                    'filename': compressed_filename
                })
            except Exception as e:
                # Hapus file sementara jika terjadi error
                if temp_file_path and os.path.exists(temp_file_path):
                    os.remove(temp_file_path)
                if compressed_path and os.path.exists(compressed_path):
                    os.remove(compressed_path)
                return jsonify({'error': f'Error saat kompresi: {str(e)}'}), 500
        
        return jsonify({'error': 'Tipe file tidak didukung'}), 400
    
    except Exception as e:
        # Hapus semua file sementara jika terjadi error
        if temp_file_path and os.path.exists(temp_file_path):
            os.remove(temp_file_path)
        if compressed_path and os.path.exists(compressed_path):
            os.remove(compressed_path)
        print(f"Unexpected error: {str(e)}")
        print(traceback.format_exc())
        return jsonify({'error': 'Terjadi kesalahan server'}), 500

@app.route('/download/<filename>')
def download_file(filename):
    try:
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        if not os.path.exists(file_path):
            return jsonify({'error': 'File tidak ditemukan'}), 404
        return send_file(file_path, as_attachment=True)
    except Exception as e:
        return jsonify({'error': f'Error saat download: {str(e)}'}), 500

@app.route('/manual')
def manual_book():
    return render_template('manual_book.html')

@app.route('/compress', methods=['POST'])
def compress_api():
    temp_file_path = None
    compressed_path = None
    try:
        if 'file' not in request.files:
            return 'Tidak ada file yang diunggah', 400
        file = request.files['file']
        if file.filename == '':
            return 'Tidak ada file yang dipilih', 400
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            temp_file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(temp_file_path)
            try:
                file_type = magic.from_file(temp_file_path, mime=True)
                compressed_data = compress_file(temp_file_path, file_type)
                # Nama file hasil kompresi
                if file_type.startswith('image/'):
                    out_filename = f"compressed_{os.path.splitext(filename)[0]}.jpg"
                    mimetype = 'image/jpeg'
                else:
                    out_filename = f"compressed_{filename}.zip"
                    mimetype = 'application/zip'
                # Hapus file asli
                if os.path.exists(temp_file_path):
                    os.remove(temp_file_path)
                return send_file(
                    io.BytesIO(compressed_data),
                    as_attachment=True,
                    download_name=out_filename,
                    mimetype=mimetype
                )
            except Exception as e:
                if temp_file_path and os.path.exists(temp_file_path):
                    os.remove(temp_file_path)
                return f'Error saat kompresi: {str(e)}', 500
        return 'Tipe file tidak didukung', 400
    except Exception as e:
        if temp_file_path and os.path.exists(temp_file_path):
            os.remove(temp_file_path)
        return 'Terjadi kesalahan server', 500

if __name__ == '__main__':
    app.run(debug=True) 
