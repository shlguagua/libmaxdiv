// g++ --std=c++11 -O3 -Wall -I../maxdiv/libmaxdiv -I/home/barz/lib/eigen-3.2.8 -I/home/barz/lib/anaconda3/include -L/home/barz/lib/anaconda3/lib -L../maxdiv/libmaxdiv/bin -Wl,-rpath,/home/barz/lib/anaconda3/lib,-rpath,/home/barz/anomaly-detection/extreme-interval-detection/maxdiv/libmaxdiv/bin -shared -o maxdiv_coastdat.so maxdiv_coastdat.cc -lmaxdiv -lnetcdf -fopenmp`

#include <iostream>
#include <algorithm>
#include <cmath>
#include <netcdf.h>
#include "libmaxdiv.h"
#include "DataTensor.h"
#include "utils.h"
using MaxDiv::DataTensor;
using MaxDiv::ReflessIndexVector;


#define COASTDAT_PATH "/home/barz/anomaly-detection/CoastDat-raw/"
#define COASTDAT_FIRST_YEAR 1958
#define COASTDAT_NUM_YEARS 50


extern "C"
{

typedef struct {
    const char * variables; /**< Comma-separated list of the variables to be read. Available variables are: dd, ds, ff, hs, mp, tm1, tm2, tp, wd */
    unsigned int firstYear; /**< First year to include in the data (ranging from 1958 to 2007 or from 1 to 50). */
    unsigned int lastYear; /**< Last year to include in the data (ranging from 1958 to 2007 or from 1 to 50). */
    unsigned int firstLat; /**< Index of the first latitude to include in the data. */
    unsigned int lastLat; /**< Index of the last latitude to include in the data. */
    unsigned int firstLon; /**< Index of the first longitude to include in the data. */
    unsigned int lastLon; /**< Index of the last longitude to include in the data. */
    unsigned int spatialPoolingSize; /**< Number of spatial cells to be aggregated. */
} coastdat_params_t;

/**
* Loads data from the CoastDat data set and applies the MaxDiv anomaly detection algorithm to it.
*
* @param[in] params Pointer to a structure with the parameters for the algorithm.
*
* @param[in] data_params Pointer to a structure specifying the portion of the data set to be read.
* The default parameters can be retrieved by calling `maxdiv_coastdat_default_params()`.
*
* @param[out] detection_buf Pointer to a buffer where the detected sub-blocks will be stored.
*
* @param[in,out] detection_buf_size Pointer to the number of elements allocated for `detection_buf`. The integer
* pointed to will be set to the actual number of elements written to the buffer.
*
* @return Returns 0 on success, a negative error code obtained from libnetcdf if the data could not be read
* or a positive error code if an internal error occurred.
*/
int maxdiv_coastdat(const maxdiv_params_t * params, const coastdat_params_t * data_params,
                    detection_t * detection_buf, unsigned int * detection_buf_size);

/**
* Determines the size of window of relevant context for a given portion of the CoastDat data set.
*
* @param[in] data_params Pointer to a structure specifying the portion of the data set to be read.
* The default parameters can be retrieved by calling `maxdiv_coastdat_default_params()`.
*
* @return Returns the context window size
*
* @see MaxDiv::TimeDelayEmbedding::determineContextWindowSize
*/
int maxdiv_coastdat_context_window_size(const coastdat_params_t * data_params);

/**
* Initializes a given `coastdat_params_t` structure with the default parameters.
*
* @param[out] data_params Pointer to the parameter structure to be set to the default parameters.
*/
void maxdiv_coastdat_default_params(coastdat_params_t * data_params);

};


int read_coastdat(const coastdat_params_t * data_params, DataTensor & coastData)
{
    int status, ncid, var_id, dim_id;
    char filename[512];
    
    // Check parameters
    if (data_params == NULL || data_params->spatialPoolingSize < 1)
        return 1;
    std::vector<std::string> variables;
    if (splitString(strtolower(data_params->variables), ",; ", variables) < 1)
        return 1;
    unsigned int firstYear = (data_params->firstYear >= COASTDAT_FIRST_YEAR) ? data_params->firstYear - COASTDAT_FIRST_YEAR + 1 : data_params->firstYear;
    unsigned int lastYear = (data_params->lastYear >= COASTDAT_FIRST_YEAR) ? data_params->lastYear - COASTDAT_FIRST_YEAR + 1 : data_params->lastYear;
    if (firstYear < 1 || lastYear < 1 || lastYear < firstYear || lastYear - firstYear + 1 > COASTDAT_NUM_YEARS)
        return 1;
    
    // Determine number of time steps
    std::size_t dim_len;
    ReflessIndexVector shape;
    shape.t = 0;
    shape.x = ceil(data_params->lastLon - data_params->firstLon + 1) / static_cast<float>(data_params->spatialPoolingSize));;
    shape.y = ceil((data_params->lastLat - data_params->firstLat + 1) / static_cast<float>(data_params->spatialPoolingSize));
    shape.z = 1;
    shape.d = variables.size();
    for (unsigned int year = firstYear; year <= lastYear; ++year)
    {
        // Open NetCDF file
        sprintf(filename, COASTDAT_PATH "%s/coastDat-1_Waves_%s_%03u.nc", variables[0].c_str(), variables[0].c_str(), year);
        status = nc_open(filename, 0, &ncid);
        if (status != NC_NOERR) return status;
        
        // Get handle to variable
        status = nc_inq_varid(ncid, variables[0], &var_id);
        if (status != NC_NOERR) return status;
        
        // Query length of time dimension
        status = nc_inq_dimid(ncid, "time", &dim_id);
        if (status != NC_NOERR) return status;
        status = nc_inq_dimlen(ncid, dim_id, &dim_len);
        if (status != NC_NOERR) return status;
        shape.t += dim_len;
        
        nc_close(ncid);
    }
    
    std::cerr << "Data shape: " << shape.t << << " x " << shape.x << " x " << shape.y << " x " << shape.z << " x " << shape.d << std::endl;
    std::cerr << "Memory usage: " << static_cast<float>(shape.prod() * sizeof(MaxDiv::Scalar)) / (1 << 30) << " GiB" << std::endl;
    
    // Read data
    coastData.resize(shape)
    DataTensor buffer;
    std::size_t dataStart[] = { 0, data_params->firstLat, data_params->firstLon };
    std::size_t dataLength[] = { 0, data_params->lastLat - data_params->firstLat + 1, data_params->lastLon - data_params->firstLon + 1 };
    std::size_t timeOffset = 0;
    for (unsigned int year = firstYear; year <= lastYear; ++year)
    {
        for (std::size_t d = 0; d < variables.size(); ++d)
        {
            // Open NetCDF file
            sprintf(filename, COASTDAT_PATH "%s/coastDat-1_Waves_%s_%03u.nc", variables[d].c_str(), variables[d].c_str(), year);
            std::cerr << "Reading " << filename << std::endl;
            status = nc_open(filename, 0, &ncid);
            if (status != NC_NOERR) return status;
            
            // Get handle to variable
            status = nc_inq_varid(ncid, variables[d], &var_id);
            if (status != NC_NOERR) return status;
            
            // Query length of time dimension
            status = nc_inq_dimid(ncid, "time", &dim_id);
            if (status != NC_NOERR) return status;
            status = nc_inq_dimlen(ncid, dim_id, &dim_len);
            if (status != NC_NOERR) return status;
            dataLength[0] = dim_len;
            
            // Read block from NetCDF file
            buffer.resize({ dara_length[0], dataLength[1], dataLength[2], 1, 1 });
            #ifdef MAXDIV_FLOAT
            status = nc_get_vara_float(ncid, var_id, dataStart, dataLength, buffer.raw());
            #else
            status = nc_get_vara_double(ncid, var_id, dataStart, dataLength, buffer.raw());
            #endif
            if (status != NC_NOERR) return status;
            
            nc_close(ncid);
            
            // Average Pooling (and swapping of Lat/Lon)
            for (std::size_t t = 0; t < dim_len; ++t)
            {
                DataTensor::ConstScalarMatrixMap timestep(buffer.raw(), buffer.width(), buffer.height(), Eigen::Stride<Eigen::Dynamic, Eigen::Dynamic>(buffer.height(), 1));
                for (DataTensor::Index x = 0; x < shape.x; ++x)
                    for (DataTensor::Index y = 0; y < shape.y; ++y)
                    {
                        // Note that latitude is mapped to the y-axis in `coastData`,
                        // but to the x-axis in `buffer`.
                        coastData({ timeOffset + t, x, y, 0, d }) = timestep.block(
                            y * data_params->spatialPoolingSize,
                            x * data_params->spatialPoolingSize,
                            std::min(data_params->spatialPoolingSize, buffer.width() - y * data_params->spatialPoolingSize),
                            std::min(data_params->spatialPoolingSize, buffer.height() - x * data_params->spatialPoolingSize)
                        ).mean();
                    }
            }
        }
        timeOffset += dim_len;
    }
    buffer.release();
    
    return 0;
}


int maxdiv_coastdat(const maxdiv_params_t * params, const coastdat_params_t * data_params,
                    detection_t * detection_buf, unsigned int * detection_buf_size)
{
    if (params == NULL)
        return 1;
    
    // Read dataset
    DataTensor coastData;
    int status = read_coastdat(data_params, coastData);
    if (status != 0) return status;
    
    // Apply MaxDiv algorithm
    auto start = std::chrono::high_resolution_clock::now();
    maxdiv(params, coastData.raw(), shape.ind, detection_buf, detection_buf_size, false);
    auto stop = std::chrono::high_resolution_clock::now();
    std::cerr << "MaxDiv algorithm took "
              << std::chrono::duration_cast<std::chrono::milliseconds>(stop - start).count() / 1000.0f
              << " s." << std::endl;
    
    return 0;
}


int maxdiv_coastdat_context_window_size(const coastdat_params_t * data_params)
{
    DataTensor coastData;
    if (read_coastdat(data_params, coastData) != 0)
        return 0;
    
    return MaxDiv::TimeDelayEmbedding().determineContextWindowSize(coastData);
}


void maxdiv_coastdat_default_params(coastdat_params_t * data_params)
{
    if (data_params == NULL)
        return;
    
    data_params->variables = "ff,hs,mp";
    data_params->firstYear = 1;
    data_params->lastYear = 50;
    data_params->firstLat = 53;
    data_params->lastLat = 100;
    data_params->firstLon = 30;
    data_params->lastLon = 98;
    data_params->spatialPoolingSize = 4;
}
