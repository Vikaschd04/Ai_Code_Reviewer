package com.shop.core.dao.impl;

import de.hybris.platform.core.model.product.ProductModel;
import de.hybris.platform.servicelayer.search.FlexibleSearchQuery;
import de.hybris.platform.servicelayer.search.FlexibleSearchService;
import de.hybris.platform.servicelayer.search.SearchResult;
import java.util.List;

public class DefaultShopProductDao {

    private FlexibleSearchService flexibleSearchService;

    // Positive: user input concatenated into FlexibleSearch.
    public List<ProductModel> findByName(final String name) {
        final FlexibleSearchQuery query =
                new FlexibleSearchQuery("SELECT {pk} FROM {Product} WHERE {name} = '" + name + "'");
        final SearchResult<ProductModel> result = flexibleSearchService.search(query);
        return result.getResult();
    }

    // Negative for injection (bound parameter) and for unbounded results (count set).
    public List<ProductModel> findByCode(final String code) {
        final FlexibleSearchQuery query =
                new FlexibleSearchQuery("SELECT {pk} FROM {Product} WHERE {code} = ?code");
        query.addQueryParameter("code", code);
        query.setCount(50);
        final SearchResult<ProductModel> result = flexibleSearchService.search(query);
        return result.getResult();
    }

    // Positive for unbounded results: every product is materialized in memory.
    public List<ProductModel> findAll() {
        final FlexibleSearchQuery query = new FlexibleSearchQuery("SELECT {pk} FROM {Product}");
        final SearchResult<ProductModel> result = flexibleSearchService.search(query);
        return result.getResult();
    }

    public void setFlexibleSearchService(final FlexibleSearchService flexibleSearchService) {
        this.flexibleSearchService = flexibleSearchService;
    }
}
